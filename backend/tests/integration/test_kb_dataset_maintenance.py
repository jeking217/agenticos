"""Editing, deleting, restoring and reading a record's own history (T15–T17).

Every one of these is a version: a maintenance action changed what the data
says, so an answer computed today must be explainable tomorrow by pointing at
the version that made it so. The tests are written against a real Postgres
because the thing that could go wrong is an interval - a row closed at the wrong
version is a row that reads as deleted for a period nobody edited.
"""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.repositories import kb_dataset as repo
from app.services.kb_dataset import maintenance, versions

from tests.integration.kb_dataset_support import base, dataset, published

pytestmark = pytest.mark.anyio

def _key(number: str) -> str:
    """The stored identity of an order number - normalised, so not `SO-1`."""
    return versions.record_key({"订单编号": number}, ["订单编号"])


ROWS = [
    {"订单编号": "SO-1", "客户编号": "C-1", "金额": 100},
    {"订单编号": "SO-2", "客户编号": "C-2", "金额": 200},
]


async def _seed(db: AsyncSession) -> Any:
    kb = await base(db)
    orders = await dataset(db, kb)
    await published(db, orders, ROWS)
    return orders


async def test_an_edit_is_its_own_version_and_the_old_one_still_reads(
    db: AsyncSession,
) -> None:
    """The reason a version exists: the number somebody quoted last month."""
    orders = await _seed(db)

    version = await maintenance.update_row(
        db,
        orders,
        record_key_value=_key("SO-1"),
        values={"金额": 150},
        reason="customer corrected the amount",
        actor_user_id=None,
    )

    assert version == 2
    assert orders.current_version_no == 2
    # The new version says 150; the version the answer was computed from still
    # says 100.
    now = await maintenance.snapshot_at(db, orders, record_key_value=_key("SO-1"), version_no=2)
    then = await maintenance.snapshot_at(db, orders, record_key_value=_key("SO-1"), version_no=1)
    assert (now or {})["values"]["金额"] == 150
    assert (then or {})["values"]["金额"] == 100


async def test_a_primary_key_cannot_be_edited_into_a_different_record(
    db: AsyncSession,
) -> None:
    """An edit that changes the key would look like an update and be a new row."""
    orders = await _seed(db)

    with pytest.raises(BadRequestError):
        await maintenance.update_row(
            db,
            orders,
            record_key_value=_key("SO-1"),
            values={"订单编号": "SO-9"},
            reason="typo",
            actor_user_id=None,
        )


async def test_an_edit_against_a_stale_version_is_refused(
    db: AsyncSession,
) -> None:
    """Two people editing a row at once: the second is told, not silently applied."""
    orders = await _seed(db)
    await maintenance.update_row(
        db,
        orders,
        record_key_value=_key("SO-1"),
        values={"金额": 150},
        reason="first",
        actor_user_id=None,
    )

    with pytest.raises(ConflictError):
        await maintenance.update_row(
            db,
            orders,
            record_key_value=_key("SO-1"),
            values={"金额": 999},
            reason="second",
            actor_user_id=None,
            expected_version=1,
        )


async def test_an_unchanged_edit_does_not_mint_a_version(
    db: AsyncSession,
) -> None:
    """Re-saving the same value is not news, and a version per no-op is noise."""
    orders = await _seed(db)

    version = await maintenance.update_row(
        db,
        orders,
        record_key_value=_key("SO-1"),
        values={"金额": 100},
        reason="opened and saved",
        actor_user_id=None,
    )

    assert version == 1
    assert orders.current_version_no == 1


async def test_a_deletion_is_a_version_with_a_tombstone_and_can_be_undone(
    db: AsyncSession,
) -> None:
    """Deleting is a fact about the data, not the absence of a row."""
    orders = await _seed(db)

    deleted = await maintenance.delete_row(
        db, orders, record_key_value=_key("SO-2"), reason="duplicate", actor_user_id=None
    )

    assert deleted == 2
    rows = await repo.rows_as_of(
        db, partition_key=orders.storage_partition_key, dataset_id=orders.id, version_no=2
    )
    assert {row.record_key: row.is_deleted for row in rows} == {_key("SO-1"): False, _key("SO-2"): True}

    restored = await maintenance.restore_row(
        db, orders, record_key_value=_key("SO-2"), reason="not a duplicate after all", actor_user_id=None
    )

    assert restored == 3
    live = await repo.rows_as_of(
        db, partition_key=orders.storage_partition_key, dataset_id=orders.id, version_no=3
    )
    assert all(not row.is_deleted for row in live)


async def test_a_row_the_file_never_had_can_be_inserted_once(
    db: AsyncSession,
) -> None:
    """An inserted record behaves like an imported one from then on - including
    being kept when a later file does not mention it."""
    orders = await _seed(db)

    version = await maintenance.insert_row(
        db,
        orders,
        values={"订单编号": "SO-3", "客户编号": "C-3", "金额": 300},
        reason="phone order",
        actor_user_id=None,
    )

    assert version == 2
    with pytest.raises(ConflictError):
        await maintenance.insert_row(
            db,
            orders,
            values={"订单编号": "SO-3", "客户编号": "C-3", "金额": 300},
            reason="same order again",
            actor_user_id=None,
        )


@pytest.mark.parametrize(
    "values",
    [
        {"利润": 1},
        {"订单编号": None},
    ],
)
async def test_a_write_that_cannot_be_a_record_is_refused(
    db: AsyncSession, values: dict[str, Any]
) -> None:
    """An unknown column, or a row with no identity: both are refusals, not rows."""
    orders = await _seed(db)

    with pytest.raises(BadRequestError):
        await maintenance.insert_row(
            db, orders, values=values, reason="x", actor_user_id=None
        )


async def test_a_row_that_was_never_there_has_no_snapshot(db: AsyncSession) -> None:
    orders = await _seed(db)

    assert (
        await maintenance.snapshot_at(db, orders, record_key_value=_key("SO-9"), version_no=1) is None
    )
    with pytest.raises(NotFoundError):
        await maintenance.update_row(
            db,
            orders,
            record_key_value=_key("SO-9"),
            values={"金额": 1},
            reason="x",
            actor_user_id=None,
        )
