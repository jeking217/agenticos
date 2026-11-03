"""An import that a worker publishes, rather than the request that asked for it.

`queued` and `running` are the two states between "somebody chose the sheets" and
"the versions exist". They are separate because a poller has to tell a job that
is still waiting for a worker from one a worker is holding: the first is normal,
the second is where the sheet in flight can be named, and only the second can be
abandoned by a process that died.

A row left in either state is not a stranded import: `settle_lost_import` moves
it to `failed`, which is a status `commit` accepts, so the retry is the same
button the person already has. The downgrade therefore folds them into `failed`
rather than refusing -- there is no earlier state they honestly describe.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0249_dataset_import_queue_status"
down_revision: str | Sequence[str] | None = "0248_kb_datasets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "kb_data"
CONSTRAINT = "dataset_imports_status_check"

_BEFORE = "status IN ('draft', 'preview', 'committed', 'partial', 'failed')"
_AFTER = "status IN ('draft', 'preview', 'queued', 'running', 'committed', 'partial', 'failed')"


def _replace(check: str) -> None:
    op.drop_constraint(CONSTRAINT, "dataset_imports", schema=SCHEMA, type_="check")
    op.create_check_constraint(CONSTRAINT, "dataset_imports", check, schema=SCHEMA)


def upgrade() -> None:
    _replace(_AFTER)


def downgrade() -> None:
    op.execute(
        f"UPDATE {SCHEMA}.dataset_imports SET status = 'failed' "
        "WHERE status IN ('queued', 'running')"
    )
    _replace(_BEFORE)
