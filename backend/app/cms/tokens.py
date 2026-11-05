"""Keeping the person's platform token, since the CMS is called on their behalf.

The console is signed in through Unione, and the CMS accepts that same token:
`TokenService.getTcm()` looks the person up by `tenant@user@md5` and verifies
the JWT against *their* password. So the token is the person, and this module
is where it is kept.

It is stored encrypted, with a key derived from the deployment's own secret,
because the token is a password in everything but name - it can publish a
customer's website. `TokenService` never writes it to a log and no API returns
it; the plaintext exists for the duration of one request.

A token that cannot be decrypted is treated as expired, not as an error. The
only causes are a rotated deployment secret or a tampered row, and in both
cases the answer for the person is the same: sign in again.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken

from app.cms.errors import TOKEN_EXPIRED, CmsError

#: Separates this use of the deployment secret from every other one. A secret
#: reused across features without a label is a secret whose compromise in one
#: place decrypts another.
_KEY_LABEL = b"agenticos.cms.session-token.v1"

#: How long before expiry a token is called stale. The CMS renews on use
#: (`tcm.autoEnable`), so a token minutes from expiry is usually fine - but a
#: release that starts at 23:59 and publishes at 00:01 is not the moment to
#: discover otherwise.
EXPIRY_SKEW = timedelta(minutes=5)


def derive_key(secret: str) -> bytes:
    """A Fernet key from the deployment's secret, labelled for this use."""
    if not secret:
        raise ValueError("storing a platform token needs a deployment secret")
    digest = hashlib.sha256(_KEY_LABEL + secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


class TokenCipher:
    """Seals a platform token for storage, and opens it for one request."""

    def __init__(self, secret: str) -> None:
        self._fernet = Fernet(derive_key(secret))

    def seal(self, token: str) -> str:
        if not token:
            raise ValueError("there is no empty token to store")
        return self._fernet.encrypt(token.encode()).decode()

    def unseal(self, cipher: str) -> str:
        """The token, or `cms_token_expired` - never a stack trace.

        A caller that gets this code shows "sign in again". A caller that got an
        `InvalidToken` would have to know to do that, and the default handling
        of an unexpected exception is a 500, which tells the person nothing.
        """
        try:
            return self._fernet.decrypt(cipher.encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise CmsError(TOKEN_EXPIRED, "the stored platform token cannot be read; sign in again") from exc


def is_expired(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    """Whether the token is too close to its end to start a publish with."""
    if expires_at is None:
        return False
    moment = now or datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at - EXPIRY_SKEW <= moment


def mask(token: str) -> str:
    """What may appear in a log line: never the token, only that there is one."""
    return f"<token:{len(token)} chars>"
