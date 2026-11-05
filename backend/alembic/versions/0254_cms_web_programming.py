"""CMS session tokens and the sites a person has opened.

`cms_session_tokens` holds one sealed platform token per person: the CMS
identifies a caller by the platform's own token, so calling it on somebody's
behalf means keeping that token. It is stored encrypted and cascades with the
user - a token for an account that no longer exists is not a token.

`cms_site_bindings` is a convenience table, not a permission one. It records
what the picker last saw about a site so "recent" does not require a network
call; authority stays with the CMS, which is asked on every write.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0254_cms_web_programming"
down_revision: str | Sequence[str] | None = "0253_drama_tickets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cms_session_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="unione"),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("token_cipher", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "platform", name="uq_cms_session_token"),
    )
    op.create_index(
        "ix_cms_session_tokens_user_id", "cms_session_tokens", ["user_id"], unique=False
    )
    op.create_index(
        "ix_cms_session_tokens_organization_id",
        "cms_session_tokens",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "cms_site_bindings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("ctx", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("domain", sa.String(length=200), nullable=True),
        sa.Column("space_id", sa.Integer(), nullable=True),
        sa.Column("space_vers", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "site_id", name="uq_cms_site_binding"),
    )
    op.create_index("ix_cms_site_bindings_user_id", "cms_site_bindings", ["user_id"], unique=False)
    op.create_index("ix_cms_site_bindings_site_id", "cms_site_bindings", ["site_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cms_site_bindings_site_id", table_name="cms_site_bindings")
    op.drop_index("ix_cms_site_bindings_user_id", table_name="cms_site_bindings")
    op.drop_table("cms_site_bindings")
    op.drop_index("ix_cms_session_tokens_organization_id", table_name="cms_session_tokens")
    op.drop_index("ix_cms_session_tokens_user_id", table_name="cms_session_tokens")
    op.drop_table("cms_session_tokens")
