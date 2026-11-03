"""A knowledge base holds several datasets, not one (T10/T34 blocker).

`datasets` was created with a unique constraint on `knowledge_base_id` alone, so
a base could hold one dataset. Everything downstream assumes otherwise: a
workbook's sheets are several datasets, a relation joins two of them, and
`find_dataset(db, kb_id, name)` - which is what refuses a duplicate name - is
meaningless with one row per base. The constraint is replaced with the one the
code already enforces: the name is unique *within* a base.

The upgrade is not data lossy in either direction. A deployment that somehow has
several rows per base could not exist under the old constraint, so nothing needs
deduplicating on the way up; and the new constraint is weaker, so nothing needs
it on the way down either.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0250_dataset_names_per_base"
down_revision: str | Sequence[str] | None = "0249_dataset_import_queue_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "kb_data"
TABLE = "datasets"
OLD = "datasets_knowledge_base_id_key"
NEW = "datasets_knowledge_base_id_name_key"


def upgrade() -> None:
    op.drop_constraint(OLD, TABLE, schema=SCHEMA, type_="unique")
    op.create_unique_constraint(NEW, TABLE, ["knowledge_base_id", "name"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_constraint(NEW, TABLE, schema=SCHEMA, type_="unique")
    op.create_unique_constraint(OLD, TABLE, ["knowledge_base_id"], schema=SCHEMA)
