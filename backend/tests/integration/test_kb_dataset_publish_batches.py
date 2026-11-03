"""Closing intervals, across the boundary where the batch splits them.

`close_rows` sends the ids in chunks because one `UPDATE … WHERE id IN (50 000
parameters)` took 1 890 seconds at a million rows - 90% of a re-import that is
otherwise three minutes. A loop is where an off-by-one lives, and the failure it
produces is the quietest one in this feature: a row whose old interval was never
closed reads at *both* versions, so a diff shows a change that is not there and
the row count is wrong by one per missed batch.

The test forces the boundary down to seven so twenty changed rows cross it twice.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.kb_dataset import DatasetRow
from app.repositories import kb_dataset as repo

from tests.integration.kb_dataset_support import ORDERS, base, dataset

pytestmark = pytest.mark.anyio

ROWS = 20


def _orders(count: int, *, amount: int) -> list[dict[str, object]]:
    return [
        {"订单编号": f"SO-{index:03d}", "客户编号": f"C-{index}", "金额": amount}
        for index in range(count)
    ]


async def test_every_interval_closes_across_a_batch_boundary(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seven ids a statement, twenty rows: three statements, and no row left open."""
    monkeypatch.setattr(repo, "CLOSE_BATCH", 7)
    kb = await base(db)
    orders = await dataset(db, kb, fields=list(ORDERS))

    published = await repo.count_current(
        db, partition_key=orders.storage_partition_key, dataset_id=orders.id
    )
    assert published == 0

    from app.services.kb_dataset.versions import publish

    first = await publish(db, orders, _orders(ROWS, amount=100), key_fields=["订单编号"])
    assert (first.rows_added, first.rows_changed) == (ROWS, 0)

    second = await publish(db, orders, _orders(ROWS, amount=200), key_fields=["订单编号"])
    # Changed, not added: the record existed and its interval was closed.
    assert (second.rows_added, second.rows_changed) == (0, ROWS)

    rows = list(
        (
            await db.execute(
                select(DatasetRow).where(
                    DatasetRow.dataset_id == orders.id,
                    DatasetRow.partition_key == orders.storage_partition_key,
                )
            )
        ).scalars()
    )
    assert len(rows) == ROWS * 2

    open_rows = [row for row in rows if row.valid_to is None]
    assert len(open_rows) == ROWS
    assert {row.valid_from for row in open_rows} == {2}
    assert all(row.values["金额"] == 200 for row in open_rows)

    closed = [row for row in rows if row.valid_to is not None]
    assert len(closed) == ROWS
    assert {row.valid_to for row in closed} == {2}, "an interval survived the close"
    assert all(row.values["金额"] == 100 for row in closed)


async def test_a_row_that_did_not_change_keeps_its_open_interval(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only what moved is closed: the same file again mints no version at all."""
    monkeypatch.setattr(repo, "CLOSE_BATCH", 3)
    kb = await base(db)
    orders = await dataset(db, kb, fields=list(ORDERS))

    from app.services.kb_dataset.versions import publish

    await publish(db, orders, _orders(ROWS, amount=100), key_fields=["订单编号"])
    unchanged = await publish(db, orders, _orders(ROWS, amount=100), key_fields=["订单编号"])

    assert unchanged.published is False
    assert unchanged.rows_unchanged == ROWS
    rows = list(
        (
            await db.execute(
                select(DatasetRow).where(
                    DatasetRow.dataset_id == orders.id,
                    DatasetRow.partition_key == orders.storage_partition_key,
                )
            )
        ).scalars()
    )
    assert len(rows) == ROWS
    assert all(row.valid_to is None for row in rows)


def test_the_batch_size_is_the_one_the_measurement_settled_on() -> None:
    """A constant that drifted back to "all of them" would pass every other test."""
    assert 100 <= repo.CLOSE_BATCH <= 5_000
