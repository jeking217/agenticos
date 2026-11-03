"""Moving a dataset off the shared pool, and taking it out again.

`dataset_rows` is a LIST-partitioned table keyed by `partition_key`, and a dataset
below the threshold writes into one of two pooled partitions chosen by
`dataset_id.int % 2` (`app.db.models.kb_dataset.pool_for`). That keeps a thousand
small datasets to two relations instead of a thousand, at the cost of two tenants
competing for the same locks. Promotion is the exit: the dataset gets a partition
key of its own, a partition for it, and its rows move across.

The move is an `UPDATE` of the partition key rather than a copy: Postgres routes a
row to a different partition when the key changes, so the rows are physically
relocated in one statement and nothing is ever duplicated in both. On a 200k-row
dataset that is a few seconds of write; it is run from a background job, in a
transaction that also updates the dataset's `storage_mode`, so a failure leaves the
dataset where it was rather than half in each place.

Dropping is the inverse and exists for a deletion that has to be physical: the
partition goes, the rows in it go with it, and every other dataset on that pool is
untouched. The plan's K13 is the reason this is a command and not a documented
`psql` incantation - a `DROP TABLE` typed against the wrong name takes the pool
with it, and nothing in the catalog would have prevented that.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text as sql_text

from app.repositories import kb_dataset as repo

SCHEMA = "kb_data"
ROWS_TABLE = f"{SCHEMA}.dataset_rows"

# Postgres takes a partition bound as a literal; it cannot be a bound parameter, so
# the key reaches the DDL as a value formatted from a `uuid.UUID` and never from
# anything a request carried. Names are checked against this before they are used.
_SAFE_IDENTIFIER = re.compile(r"^(?:[a-z_][a-z0-9_]*\.)?[a-z_][a-z0-9_]*$")

# A dataset promoted once keeps this name forever: renaming a partition means
# detaching and re-attaching it, which is a longer outage than the promotion saved.
PARTITION_PREFIX = "dataset_rows_ds_"

# What the sweep treats as heavy enough to deserve its own relation. A 500k-row
# dataset is the import ceiling, so a dataset past this is one that has been
# re-imported several times; below it, two tenants on a pool is cheaper than two
# relations.
PROMOTION_ROW_THRESHOLD = 500_000

# Share of a pool's rows one database should hold before the pool is considered
# misallocated. 70% is the plan's K13 trigger: above it, "pooled" no longer means
# "shared" and the deltas from one tenant dominate every vacuum on the pool.
POOL_SHARE_THRESHOLD = 0.70


def partition_name(partition_key: uuid.UUID) -> str:
    """The relation a partition key gets. Deterministic, so promotion is idempotent."""

    return f"{PARTITION_PREFIX}{partition_key.hex[:12]}"


def _checked(identifier: str) -> str:
    """Refuse an identifier that could not be a plain table name.

    Everything this module interpolates is either a constant above or an identifier
    that passed here; the values themselves always travel as parameters.
    """

    if not _SAFE_IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"refusing to interpolate {identifier!r} into DDL")
    return identifier


def _schema_of(table: str) -> str:
    """The schema a table reference names, defaulting to `kb_data`."""

    return _checked(table).split(".")[0] if "." in table else SCHEMA


@dataclass(frozen=True)
class Candidate:
    """A dataset the sweep would move off the pool."""

    dataset_id: uuid.UUID
    rows: int
    reason: str


def candidates(
    rows_by_dataset: dict[uuid.UUID, int],
    *,
    pool_rows: int,
    threshold: int = PROMOTION_ROW_THRESHOLD,
    share: float = POOL_SHARE_THRESHOLD,
) -> list[Candidate]:
    """Which datasets to promote, largest first.

    Two rules, and the second is about the pool rather than the dataset: when one
    dataset holds most of a pool, the pool's header and its vacuum cost are really
    that dataset's, so *it* should own the relation even if no single import was
    large. Once the largest is promoted the share recomputes on the next run, which
    is how a pool drains one tenant at a time instead of all at once.
    """

    if not rows_by_dataset:
        return []

    ordered = sorted(rows_by_dataset.items(), key=lambda item: item[1], reverse=True)
    # The share rule needs something to be a share *of*. A pool holding one dataset
    # is at 100% by definition, and promoting it because of that would promote the
    # first dataset ever imported - so the comparison only applies once a pool is
    # actually shared.
    compared = len(ordered) > 1

    found: list[Candidate] = []
    for dataset_id, rows in ordered:
        if rows >= threshold:
            found.append(Candidate(dataset_id=dataset_id, rows=rows, reason=f"{rows} rows"))
        elif compared and pool_rows and rows / pool_rows >= share:
            found.append(
                Candidate(
                    dataset_id=dataset_id,
                    rows=rows,
                    reason=f"holds {rows * 100 // pool_rows}% of its pool",
                )
            )

    return found


async def create_partition(db: Any, partition_key: uuid.UUID, *, table: str = ROWS_TABLE) -> str:
    """Attach a partition for this key, or leave the one already there.

    `IF NOT EXISTS` is what makes the promotion retryable: the job can be run again
    after a crash between the DDL and the row move without failing on the first
    statement.
    """

    schema = _schema_of(table)
    name = partition_name(partition_key)
    await db.execute(
        sql_text(
            f"CREATE TABLE IF NOT EXISTS {_checked(schema)}.{_checked(name)} "
            f"PARTITION OF {_checked(table)} FOR VALUES IN ('{partition_key}')"
        )
    )
    return name


async def promote_rows(
    db: Any,
    *,
    dataset_id: uuid.UUID,
    old_key: uuid.UUID,
    new_key: uuid.UUID,
    table: str = ROWS_TABLE,
) -> int:
    """Move a dataset's rows into its own partition, returning how many moved.

    Both keys are parameters; the partition is created first so the destination
    exists before the first row is routed into it. The caller owns the transaction
    and commits the dataset's `storage_mode`/`storage_partition_key` in the same
    one - a commit that landed the rows but not the pointer would leave the dataset
    reading from a partition it no longer writes to.

    The `old_key` predicate is not decoration: it is what keeps a re-run from
    moving a dataset that has already been promoted a second time.
    """

    if old_key == new_key:
        raise ValueError("promotion has to move the dataset to a different partition key")

    await create_partition(db, new_key, table=table)
    result = await db.execute(
        sql_text(
            f"UPDATE {_checked(table)} SET partition_key = :new_key "
            "WHERE dataset_id = :dataset_id AND partition_key = :old_key"
        ),
        {"new_key": new_key, "dataset_id": dataset_id, "old_key": old_key},
    )
    return int(result.rowcount or 0)


async def drop_partition(db: Any, partition_key: uuid.UUID, *, table: str = ROWS_TABLE) -> bool:
    """Take a promoted dataset's partition out, rows and all.

    Returns whether a relation was actually dropped, because a deletion that reports
    success while nothing was removed is the failure this check exists to catch.
    A dataset still on a pool must be emptied with a `DELETE` instead: its partition
    holds other tenants, and dropping it would take them too.
    """

    schema = _schema_of(table)
    name = partition_name(partition_key)
    exists = (
        await db.execute(
            sql_text(
                "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relname = :name AND c.relispartition)"
            ),
            {"schema": schema, "name": name},
        )
    ).scalar_one()
    if not exists:
        return False

    await db.execute(sql_text(f"DROP TABLE {_checked(schema)}.{_checked(name)}"))
    return True


async def delete_pooled_rows(db: Any, dataset_id: uuid.UUID, *, table: str = ROWS_TABLE) -> int:
    """Empty a dataset that never left its pool.

    Only that dataset's rows go; the partition and every other tenant on it stay.
    """

    result = await db.execute(
        sql_text(f"DELETE FROM {_checked(table)} WHERE dataset_id = :dataset_id"),
        {"dataset_id": dataset_id},
    )
    return int(result.rowcount or 0)


async def promote_dataset(
    db: Any, dataset: Any, *, new_key: uuid.UUID | None = None, table: str = ROWS_TABLE
) -> int:
    """Give one dataset its own relation, returning how many rows moved.

    The row move and the pointer are one transaction, and the pointer is the
    reason this is not just a call to `promote_rows`: a dataset whose rows are on
    a dedicated partition while `storage_partition_key` still names the pool
    reads nothing, and nothing in the schema would have complained.

    The new key is fresh rather than derived from the dataset: a key derived from
    the id would collide with the pool's own selection when `id % 2` pointed at
    it, and the promotion would then move rows onto the pool it was leaving.
    """

    old_key = dataset.storage_partition_key
    target = new_key if new_key is not None else uuid.uuid4()
    moved = await promote_rows(
        db, dataset_id=dataset.id, old_key=old_key, new_key=target, table=table
    )
    await repo.set_storage(db, dataset.id, mode="dedicated", partition_key=target)
    return moved


async def detach_partition(db: Any, partition_key: uuid.UUID, *, table: str = ROWS_TABLE) -> bool:
    """Unhook a partition from its parent, keeping the relation and its rows.

    This is the physical half of archiving: `archived_at` stops the application
    from answering questions out of the dataset, and the detach is what makes the
    rows a `pg_dump` of one table rather than part of every query plan on the
    parent. Returns whether a partition was actually detached, for the same
    reason `drop_partition` does - a silent no-op here is discovered months
    later, by a backup that does not contain what it was supposed to.

    Not `DETACH PARTITION CONCURRENTLY`: that form cannot run inside a
    transaction, and this is called from inside one. The cost is a brief
    `ACCESS EXCLUSIVE` lock on the parent, which is acceptable for an operation
    an operator runs for one dataset and documents afterwards.
    """

    schema = _schema_of(table)
    name = partition_name(partition_key)
    exists = (
        await db.execute(
            sql_text(
                "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relname = :name AND c.relispartition)"
            ),
            {"schema": schema, "name": name},
        )
    ).scalar_one()
    if not exists:
        return False

    await db.execute(
        sql_text(
            f"ALTER TABLE {_checked(table)} DETACH PARTITION {_checked(schema)}.{_checked(name)}"
        )
    )
    return True


async def archive_dataset(db: Any, dataset: Any, *, table: str = ROWS_TABLE) -> bool:
    """Freeze a promoted dataset's rows out of the parent, rows and all kept.

    Only a dataset that owns its partition can be archived this way: a pooled one
    shares its relation with other tenants, so there is nothing to detach that
    would not take them as well. Returns whether the detach happened.
    """

    if dataset.storage_mode != "dedicated":
        raise ValueError("only a dataset with its own partition can be archived")
    return await detach_partition(db, dataset.storage_partition_key, table=table)


async def partition_rows(db: Any, *, table: str = ROWS_TABLE) -> dict[str, int]:
    """Live row estimates per partition, for the promotion decision."""

    rows = (
        await db.execute(
            sql_text(
                "SELECT c.relname, coalesce(s.n_live_tup, 0) "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid "
                "WHERE n.nspname = :schema AND c.relispartition AND c.relkind IN ('r', 'p')"
            ),
            {"schema": _schema_of(table)},
        )
    ).all()
    return {row[0]: int(row[1]) for row in rows}
