"""Promotion has to move rows, not copy them, and dropping has to be local.

These run against a real Postgres because the thing under test *is* the physical
behaviour: a LIST partition routes a row by its key, `UPDATE partition_key` makes
Postgres relocate that row, and `DROP TABLE` on a partition removes exactly its
own rows. None of that can be faked with a session stub, and all three are what a
promotion or a deletion depends on.

The table is created here rather than reusing `kb_data.dataset_rows`: the
integration suite builds the schema from the models, where `dataset_rows` is an
ordinary table with no partitions, so a promotion against it would prove nothing.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.kb_dataset import partitions

pytestmark = pytest.mark.anyio

TABLE = "kb_data.dataset_rows_partition_test"


@pytest.fixture
async def partition_table(db: AsyncSession):
    """A partitioned table shaped like the real one, with one pooled partition."""

    pool = uuid.uuid4()
    await db.execute(
        text(
            f"""
            CREATE TABLE {TABLE} (
                partition_key uuid NOT NULL,
                dataset_id uuid NOT NULL,
                id uuid NOT NULL,
                record_key text NOT NULL,
                PRIMARY KEY (partition_key, id)
            ) PARTITION BY LIST (partition_key)
            """
        )
    )
    await db.execute(
        text(f"CREATE TABLE {TABLE}_pool PARTITION OF {TABLE} FOR VALUES IN ('{pool}')")
    )
    await db.execute(
        text(
            f"INSERT INTO {TABLE} (partition_key, dataset_id, id, record_key) "
            "VALUES (:pool, :one, gen_random_uuid(), 'a'), (:pool, :one, gen_random_uuid(), 'b'), "
            "(:pool, :two, gen_random_uuid(), 'c')"
        ),
        {"pool": pool, "one": uuid.uuid4(), "two": uuid.uuid4()},
    )
    await db.commit()

    try:
        yield pool
    finally:
        # A test that failed mid-statement leaves the transaction aborted, and an
        # aborted transaction refuses the `DROP` - which used to leave the scratch
        # table behind for the next run to trip over.
        await db.rollback()
        await db.execute(text(f"DROP TABLE IF EXISTS {TABLE} CASCADE"))
        await db.commit()


async def _rows(db: AsyncSession, dataset_id: uuid.UUID) -> int:
    return (
        await db.execute(
            text(f"SELECT count(*) FROM {TABLE} WHERE dataset_id = :id"), {"id": dataset_id}
        )
    ).scalar_one()


async def _physically_in(db: AsyncSession, relation: str, dataset_id: uuid.UUID) -> int:
    """Rows in one relation, bypassing the parent's routing.

    `SELECT ... FROM parent` would answer the same for a dataset whose rows are in
    the wrong partition, so the assertion has to name the child. The relation is
    passed whole because a promoted partition is named for its key alone
    (`kb_data.dataset_rows_ds_<hex>`, which is what `create_partition` creates),
    while the fixture's pool is named after the scratch table.
    """

    return (
        await db.execute(
            text(f"SELECT count(*) FROM {relation} WHERE dataset_id = :id"), {"id": dataset_id}
        )
    ).scalar_one()


async def test_promotion_relocates_the_rows_and_leaves_the_neighbour(
    db: AsyncSession, partition_table: uuid.UUID
) -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text(
            f"INSERT INTO {TABLE} (partition_key, dataset_id, id, record_key) VALUES "
            "(:pool, :first, gen_random_uuid(), 'x'), (:pool, :first, gen_random_uuid(), 'y'), "
            "(:pool, :second, gen_random_uuid(), 'z')"
        ),
        {"pool": partition_table, "first": first, "second": second},
    )
    await db.commit()

    new_key = uuid.uuid4()
    moved = await partitions.promote_rows(
        db, dataset_id=first, old_key=partition_table, new_key=new_key, table=TABLE
    )
    await db.commit()

    assert moved == 2
    # The rows are the same rows, under a different relation - not duplicates.
    assert await _rows(db, first) == 2
    assert await _physically_in(db, f"{TABLE}_pool", first) == 0
    assert (
        await _physically_in(db, f"{partitions.SCHEMA}.{partitions.partition_name(new_key)}", first)
        == 2
    )
    # The neighbour on the pool never moved.
    assert await _physically_in(db, f"{TABLE}_pool", second) == 1


async def test_promoting_twice_moves_nothing_the_second_time(
    db: AsyncSession, partition_table: uuid.UUID
) -> None:
    """The job is retried after a crash; a re-run must not fail or double-move."""

    dataset = uuid.uuid4()
    await db.execute(
        text(
            f"INSERT INTO {TABLE} (partition_key, dataset_id, id, record_key) "
            "VALUES (:pool, :ds, gen_random_uuid(), 'a')"
        ),
        {"pool": partition_table, "ds": dataset},
    )
    await db.commit()

    new_key = uuid.uuid4()
    first = await partitions.promote_rows(
        db, dataset_id=dataset, old_key=partition_table, new_key=new_key, table=TABLE
    )
    await db.commit()
    second = await partitions.promote_rows(
        db, dataset_id=dataset, old_key=partition_table, new_key=new_key, table=TABLE
    )
    await db.commit()

    assert first == 1
    assert second == 0
    assert await _rows(db, dataset) == 1


async def test_dropping_a_partition_leaves_other_datasets_alone(
    db: AsyncSession, partition_table: uuid.UUID
) -> None:
    promoted, pooled = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text(
            f"INSERT INTO {TABLE} (partition_key, dataset_id, id, record_key) VALUES "
            "(:pool, :promoted, gen_random_uuid(), 'a'), (:pool, :pooled, gen_random_uuid(), 'b')"
        ),
        {"pool": partition_table, "promoted": promoted, "pooled": pooled},
    )
    await db.commit()

    new_key = uuid.uuid4()
    await partitions.promote_rows(
        db, dataset_id=promoted, old_key=partition_table, new_key=new_key, table=TABLE
    )
    await db.commit()

    dropped = await partitions.drop_partition(db, new_key, table=TABLE)
    await db.commit()

    assert dropped is True
    assert await _rows(db, promoted) == 0
    assert await _rows(db, pooled) == 1


async def test_dropping_a_partition_that_is_not_there_reports_it(
    db: AsyncSession, partition_table: uuid.UUID
) -> None:
    """A deletion that reports success while nothing was removed is the failure."""

    assert await partitions.drop_partition(db, uuid.uuid4(), table=TABLE) is False


async def test_a_pooled_dataset_is_emptied_without_dropping_the_pool(
    db: AsyncSession, partition_table: uuid.UUID
) -> None:
    """The pool holds other tenants; emptying one dataset must not take them."""

    first, second = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text(
            f"INSERT INTO {TABLE} (partition_key, dataset_id, id, record_key) VALUES "
            "(:pool, :first, gen_random_uuid(), 'a'), (:pool, :second, gen_random_uuid(), 'b')"
        ),
        {"pool": partition_table, "first": first, "second": second},
    )
    await db.commit()

    removed = await partitions.delete_pooled_rows(db, first, table=TABLE)
    await db.commit()

    assert removed == 1
    assert await _rows(db, first) == 0
    assert await _rows(db, second) == 1


def test_a_partition_name_is_derived_from_the_key() -> None:
    # A realistic key: the name is the first 12 hex digits, and a key that is all
    # zeros in front would make that assertion about the format rather than about
    # the derivation.
    key = uuid.UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")

    assert partitions.partition_name(key) == "dataset_rows_ds_7c9e66797425"
    # Deterministic, which is what makes a retried promotion find its own table.
    assert partitions.partition_name(key) == partitions.partition_name(key)


def test_an_identifier_that_is_not_a_table_name_is_refused() -> None:
    """The names reach DDL by interpolation, so they are checked, not trusted."""

    session = object()
    with pytest.raises(ValueError):
        # Async, but the guard runs before the first await.
        import asyncio

        asyncio.run(
            partitions.create_partition(
                session,  # type: ignore[arg-type]
                uuid.uuid4(),
                table="kb_data.rows; DROP TABLE users",
            )
        )


def _dataset(dataset_id: uuid.UUID, key: uuid.UUID, *, mode: str = "pooled"):
    """The three attributes `promote_dataset` reads, without persisting a row."""

    from types import SimpleNamespace

    return SimpleNamespace(id=dataset_id, storage_partition_key=key, storage_mode=mode)


async def test_promoting_a_dataset_points_it_at_its_new_relation(
    db: AsyncSession, partition_table: uuid.UUID
) -> None:
    """The row move and the pointer are one decision; only one of them can be wrong."""

    dataset_id = uuid.uuid4()
    await db.execute(
        text(
            f"INSERT INTO {TABLE} (partition_key, dataset_id, id, record_key) VALUES "
            "(:pool, :ds, gen_random_uuid(), 'a'), (:pool, :ds, gen_random_uuid(), 'b')"
        ),
        {"pool": partition_table, "ds": dataset_id},
    )
    await db.commit()

    dataset = _dataset(dataset_id, partition_table)
    new_key = uuid.uuid4()
    moved = await partitions.promote_dataset(db, dataset, new_key=new_key, table=TABLE)
    await db.commit()

    assert moved == 2
    assert await _rows(db, dataset_id) == 2
    assert (
        await _physically_in(
            db, f"{partitions.SCHEMA}.{partitions.partition_name(new_key)}", dataset_id
        )
        == 2
    )


async def test_archiving_detaches_the_relation_and_keeps_its_rows(
    db: AsyncSession, partition_table: uuid.UUID
) -> None:
    """Archiving takes the dataset out of every query without taking its history away."""

    archived, neighbour = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text(
            f"INSERT INTO {TABLE} (partition_key, dataset_id, id, record_key) VALUES "
            "(:pool, :archived, gen_random_uuid(), 'a'), (:pool, :neighbour, gen_random_uuid(), 'b')"
        ),
        {"pool": partition_table, "archived": archived, "neighbour": neighbour},
    )
    await db.commit()

    own_key = uuid.uuid4()
    await partitions.promote_dataset(
        db, _dataset(archived, partition_table), new_key=own_key, table=TABLE
    )
    await db.commit()

    assert (
        await partitions.archive_dataset(
            db, _dataset(archived, own_key, mode="dedicated"), table=TABLE
        )
        is True
    )
    await db.commit()

    # The parent no longer routes to it, and the relation is still there holding
    # the rows - which is what a dump of one table picks up.
    assert await _rows(db, archived) == 0
    assert (
        await _physically_in(
            db, f"{partitions.SCHEMA}.{partitions.partition_name(own_key)}", archived
        )
        == 1
    )
    # The pooled neighbour was never involved.
    assert await _rows(db, neighbour) == 1
