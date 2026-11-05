"""The two rows this feature keeps: a person's CMS token, and the sites they opened.

A person's platform token is kept because the CMS is called on their behalf and
`TokenService` identifies them by it. It is stored encrypted (see
`app.cms.tokens`) and cascades with the user: a token for an account that no
longer exists is not a token.

`cms_site_bindings` is not a permission table. It records which sites a person
has opened and what the console knew about them at the time - a title and a
space id - so the picker has something to show before the CMS answers, and so
"recent sites" does not require a network call. Authority stays where it
already is: the CMS decides who may edit what, and this deployment asks it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CmsSessionToken(Base, TimestampMixin):
    """One platform token per person per platform, sealed."""

    __tablename__ = "cms_session_tokens"
    __table_args__ = (UniqueConstraint("user_id", "platform", name="uq_cms_session_token"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="unione")
    #: The CMS tenant this token belongs to, read from the token's own identity
    #: at sign-in. Kept so a mis-bound deployment is visible rather than silent.
    tenant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - never print the cipher
        return f"<CmsSessionToken(user_id={self.user_id}, platform={self.platform})>"


class CmsSiteBinding(Base, TimestampMixin):
    """A site a person opened, as last seen."""

    __tablename__ = "cms_site_bindings"
    __table_args__ = (UniqueConstraint("user_id", "site_id", name="uq_cms_site_binding"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    site_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    ctx: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    domain: Mapped[str | None] = mapped_column(String(200), nullable=True)
    space_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    space_vers: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<CmsSiteBinding(site_id={self.site_id}, user_id={self.user_id})>"
