"""One-time tickets that hand a signed-in person to the drama app (#2302).

The console is signed in here; the drama app is a separate program on the same
computer, with its own session of its own. Rather than asking the person to sign
in again, the console mints a ticket, passes it on the command line, and the
drama app trades it for the identity behind it - the same shape as the `tk-`
ticket the Unione portal hands a browser, and single-use for the same reason: a
ticket that outlived one redemption would be a password written in a process
list.

Only the ticket's holder can spend it, so it is stored in clear rather than
hashed: it is a 120-second secret that never leaves the machine it was minted
on, and the lookup that spends it has to be by value.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DramaTicket(Base, TimestampMixin):
    """A ticket, who it was minted for, and when it stops being spendable."""

    __tablename__ = "drama_tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: `tk-` followed by 32 hex digits, the prefix the platform's own tickets use.
    ticket: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set by the redemption itself, in the same statement that reads the row, so a
    #: second redemption of one ticket loses the race rather than replaying it.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<DramaTicket(user_id={self.user_id}, used_at={self.used_at})>"
