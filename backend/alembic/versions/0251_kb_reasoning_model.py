"""A dataset base names the model that reads its workbooks (T17 follow-up).

A dataset base is created with `embedding_*` sentinels because nothing is
vectorised; the model it *does* use is a chat model, and it uses it for a
different job - reading a sheet and saying what its columns mean. That is a
separate choice from the embedding one, on a different set of providers, and it
is optional: a base with no model still imports, on the parser's rules alone.

Four columns rather than a `model_profiles` foreign key. A profile is an
organization's named answer to "which model do agents run on", with fallbacks and
an API choice that an import has no opinion about; this is one provider, one
model id, and the key or address that reaches it - the same four fields a profile
holds, resolved directly, so deleting a profile cannot silently take away a
base's ability to read a workbook it already imported.

`reasoning_model` is free text on purpose: providers ship model ids weekly, and
the point of the field is to name one this build's catalog has not heard of yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0251_kb_reasoning_model"
down_revision: str | Sequence[str] | None = "0250_dataset_names_per_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column("reasoning_provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("reasoning_model", sa.String(length=128), nullable=True),
    )
    # The organization vault key this base's analysis runs on. SET NULL on
    # delete for the same reason as `embedding_secret_id`: a deleted secret
    # reads as "none chosen" rather than as a key that went missing.
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "reasoning_secret_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_secrets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Where a self-hosted provider answers - an Ollama or a vLLM on this
    # network. Free text rather than a `local_services` row because those are
    # kinded embedding/ocr and a chat server is neither; the field is typed in
    # the same dialog as the model id it belongs beside.
    op.add_column(
        "knowledge_bases",
        sa.Column("reasoning_base_url", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "reasoning_base_url")
    op.drop_column("knowledge_bases", "reasoning_secret_id")
    op.drop_column("knowledge_bases", "reasoning_model")
    op.drop_column("knowledge_bases", "reasoning_provider")
