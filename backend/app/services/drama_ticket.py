"""Minting and spending the one-time ticket that signs the drama app in (#2302).

The console is already signed in; the drama app is a separate program with a
session of its own. Handing it a password would be handing it the person's
password - so it is handed a ticket instead: minted for one person, valid for two
minutes, spendable once, and worthless to anyone who reads it out of a process
list five seconds later.

The redemption is a single UPDATE with a `RETURNING` clause. Two redemptions of
one ticket racing each other both succeed at the `SELECT` a naive implementation
would do first; the row lock the UPDATE takes is what makes the second one find
nothing to update, which is the whole of "single use".
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.drama_ticket import DramaTicket
from app.db.models.user import User

logger = logging.getLogger(__name__)

#: The platform's own prefix for a one-time ticket. The app on the other end
#: checks it before it sends anything, so a typo in the launch command fails
#: locally instead of as a 401 nobody can read.
TICKET_PREFIX = "tk-"

#: Two minutes: long enough for a process to start and one HTTP call, short
#: enough that a ticket left in a shell history is not a key.
TICKET_TTL = timedelta(seconds=120)

#: Where the app redeems, relative to this deployment's address.
REDEEM_PATH = "/api/drama/access"

#: Rows are kept for a day after they expire - long enough to answer "was this
#: ever spent" - and only swept when the person asks for a new one.
_TICKET_RETENTION = timedelta(days=1)


def _stand_in_user_id(user_id: UUID) -> int:
    """A stable positive int for a person the platform never named.

    The app's session is keyed on a numeric platform id. Somebody who signed in
    with this deployment's own password has none, so the first eight hex digits
    of their uuid stand in: unique enough for one deployment, stable across
    restarts, and never mistaken for a platform id because the tenant is 0.
    """
    return int(str(user_id).replace("-", "")[:8], 16) & 0x7FFFFFFF


def platform_identity(user: User) -> tuple[int, int]:
    """The `(tenantId, userId)` this person is known by, platform or stand-in.

    Accounts the Unione bridge created carry the platform's `{tenantId}:{id}`
    under `oauth_provider`/`oauth_id`, so a ticket redeems to the same identity
    the app would have got from the portal itself. Everything else falls back to
    tenant 0, which no Unione tenant can be.
    """
    if user.oauth_provider == "unione" and user.oauth_id:
        tenant, _, subject = user.oauth_id.partition(":")
        try:
            return int(tenant), int(subject)
        except ValueError:
            logger.warning("drama_ticket_unreadable_subject", extra={"user_id": str(user.id)})
    return 0, _stand_in_user_id(user.id)


def username_for(user: User) -> str:
    """The name the app shows beside the person's own: the local part of the address."""
    local = (user.email or "").split("@", 1)[0].strip()
    return local or "user"


class DramaTicketService:
    """Tickets for the drama app, minted for one person and spent once."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def issue(self, user_id: UUID) -> tuple[str, datetime]:
        """Mint a ticket for this person, sweeping the ones this deployment outlived."""
        await self.db.execute(
            delete(DramaTicket).where(
                DramaTicket.user_id == user_id,
                DramaTicket.expires_at < func.now() - _TICKET_RETENTION,
            )
        )
        ticket = f"{TICKET_PREFIX}{secrets.token_hex(16)}"
        expires_at = datetime.now(UTC) + TICKET_TTL
        self.db.add(DramaTicket(ticket=ticket, user_id=user_id, expires_at=expires_at))
        await self.db.flush()
        logger.info("drama_ticket_issued", extra={"user_id": str(user_id)})
        return ticket, expires_at

    async def redeem(self, ticket: str) -> User | None:
        """Spend the ticket and hand back who it was minted for, or `None`.

        `None` covers every refusal - unknown, expired, already spent - because
        the app is told one thing either way and the difference is only what an
        attacker would like to learn.
        """
        redemption = (
            update(DramaTicket)
            .where(
                DramaTicket.ticket == ticket,
                DramaTicket.used_at.is_(None),
                DramaTicket.expires_at > func.now(),
            )
            .values(used_at=func.now())
            .returning(DramaTicket.user_id)
        )
        user_id = (await self.db.execute(redemption)).scalar_one_or_none()
        if user_id is None:
            logger.info("drama_ticket_refused")
            return None
        user = await self.db.get(User, user_id)
        if user is None or not user.is_active:
            # The ticket was good, the person is gone: spend it anyway rather than
            # leaving a live ticket for an account that can no longer sign in.
            logger.info("drama_ticket_no_account", extra={"user_id": str(user_id)})
            return None
        logger.info("drama_ticket_redeemed", extra={"user_id": str(user_id)})
        return user

    async def unused_for(self, user_id: UUID) -> int:
        """How many live tickets this person has - shown to nobody, used by tests."""
        count = await self.db.scalar(
            select(func.count())
            .select_from(DramaTicket)
            .where(
                DramaTicket.user_id == user_id,
                DramaTicket.used_at.is_(None),
                DramaTicket.expires_at > func.now(),
            )
        )
        return int(count or 0)
