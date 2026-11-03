"""Turning the platform's identity into an account here (#2101).

The platform knows a person by `{tenantId}:{id}`, and that pair is what a local
account is keyed on - under `oauth_provider="unione"`, the columns this table
already has for exactly this. No migration, and the identity is stable across a
rename: a username changes, an id does not.

First sign-in creates the account, through the same door an OIDC or directory
sign-in uses, which means it passes the deployment's sign-up policy: a closed or
domain-limited deployment is not opened by a bridge into it.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthenticationError, AuthorizationError, ConflictError
from app.db.models.user import User
from app.repositories import user_repo
from app.services.unione.client import UnioneIdentity
from app.services.user import UserService

logger = logging.getLogger(__name__)

#: The provider name the platform's identities are stored under.
PROVIDER = "unione"

#: The domain synthesized addresses live in. `.internal` is reserved for private use,
#: so an address minted here cannot be mailed by accident - it is an identity handle,
#: not a mailbox - and unlike `.local` it is not one of RFC 6761's special-use names,
#: which `email-validator` refuses. `.local` cost every platform account a 500 from
#: `/auth/me`, which validates this column through `EmailStr` (#2101).
_EMAIL_DOMAIN = "unione.internal"

#: The longest local part this mints before falling back to the numeric id. `users.email`
#: is 255 characters, so this is not about the column: it is the point past which a
#: username stops looking like an address and starts being a DN or a UUID.
_MAX_LOCAL_PART = 40

_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def email_for(identity: UnioneIdentity) -> str:
    """The address this identity is given here, by the rule the design fixed.

    The platform's own address when it reports one; otherwise `{username}@unione.internal`,
    or `{id}@unione.internal` when the username is too long to be a local part. The local
    part is lower-cased and stripped of anything an address cannot carry, because a
    username is not required to be one - a Chinese display name would otherwise be
    stored as an address that fails validation on the way back out of `/auth/me`.
    """
    if identity.email:
        return identity.email.strip().lower()

    local = _UNSAFE.sub("-", identity.username.strip().lower()).strip("-.")
    if not local or len(local) > _MAX_LOCAL_PART or identity.user_id is None:
        local = str(identity.user_id)
    return f"{local}@{_EMAIL_DOMAIN}"


class UnioneSignInService:
    """The local account behind a platform identity, created on first sign-in."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def account_for(self, identity: UnioneIdentity) -> User:
        """The account this identity signs in as.

        Raises:
            AuthenticationError: The matched local account is deactivated.
            AuthorizationError: No account exists and this deployment does not create one.
            ConflictError: A local account already holds the address this identity maps to.
        """
        existing = await user_repo.get_by_oauth(self.db, PROVIDER, identity.subject)
        if existing is not None:
            return self._usable(existing)

        email = email_for(identity)

        # A local account holding this address is somebody who was here before the
        # bridge. Taking it over on the strength of an address alone would hand one
        # deployment's account to whoever controls that name in the platform, so this
        # is a refusal with the fix in it: an administrator links the two, once,
        # deliberately. The bridge cannot distinguish the account's owner from a
        # stranger who registered their address first.
        if await user_repo.get_by_email(self.db, email) is not None:
            logger.warning("unione_email_conflict", extra={"email_domain": _EMAIL_DOMAIN})
            raise ConflictError(
                message=(
                    "An account already exists here with the address this account would use. "
                    "Ask an administrator to link the two."
                )
            )

        if not settings.UNIONE_AUTO_PROVISION:
            raise AuthorizationError(
                message="You do not have an account here yet. Ask an administrator to invite you."
            )

        # The same factory the OIDC and directory paths use, so the sign-up policy,
        # the first-account bootstrap, the default organization and the welcome mail
        # are all the ones this deployment already decided on. The address match above
        # means this can only take its create branch.
        user = await UserService(self.db).get_or_create_oauth_user(
            provider=PROVIDER,
            provider_id=identity.subject,
            email=email,
            full_name=identity.full_name or identity.username,
        )
        logger.info("unione_account_provisioned", extra={"user_id": str(user.id)})
        return self._usable(user)

    @staticmethod
    def _usable(user: User) -> User:
        """The account, unless it has been deactivated here.

        The platform being willing to authenticate somebody says nothing about
        whether this deployment still wants them; offboarding happens in both
        places and this is where the second one is enforced.
        """
        if not user.is_active:
            raise AuthenticationError(message="User account is disabled")
        return user
