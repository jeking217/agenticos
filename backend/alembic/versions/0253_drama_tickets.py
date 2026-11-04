"""One-time tickets that hand a signed-in person to the drama app (#2302).

`drama_tickets` holds one row per ticket the console minted: the `tk-` value, the
person it was minted for, when it stops being spendable and, once it has been, when
it was spent. The row cascades with its user - a ticket for an account that no
longer exists is not a ticket.

The unique index on `ticket` is what makes the redemption's `UPDATE ... RETURNING`
single-use rather than merely usually-single-use: two concurrent redemptions cannot
both match a row that one of them has already claimed. The index on `user_id` serves
the sweep that clears a person's expired rows when they ask for a new ticket.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0253_drama_tickets"
down_revision: str | Sequence[str] | None = "0252_unione_email_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drama_tickets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ticket", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("drama_tickets_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("drama_tickets_pkey")),
    )
    op.create_index(op.f("drama_tickets_ticket_idx"), "drama_tickets", ["ticket"], unique=True)
    op.create_index(op.f("drama_tickets_user_id_idx"), "drama_tickets", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("drama_tickets_user_id_idx"), table_name="drama_tickets")
    op.drop_index(op.f("drama_tickets_ticket_idx"), table_name="drama_tickets")
    op.drop_table("drama_tickets")
