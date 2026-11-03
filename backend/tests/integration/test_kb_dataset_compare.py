"""Two versions side by side, in the four terms a person uses (T36).

The distinction this pins is the one the module exists for: a record that was
*deleted* through maintenance has a tombstone explaining it, while one that is
simply absent has none. Calling both "deleted" would put a word in the data's
mouth, and the difference is exactly what a reader of the diff needs.
"""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.services.kb_dataset import compare, maintenance, versions

from tests.integration.kb_dataset_support import base, dataset, published

pytestmark = pytest.mark.anyio


def _key(number: str) -> str:
    return versions.record_key({"订单编号": number}, ["订单编号"])


async def _two_versions(db: AsyncSession) -> Any:
    kb = await base(db)
    orders = await dataset(db, kb)
    await published(
        db,
        orders,
        [
            {"订单编号": "SO-1", "客户编号": "C-1", "金额": 100},
            {"订单编号": "SO-2", "客户编号": "C-2", "金额": 200},
        ],
    )
    await published(
        db,
        orders,
        [
            {"订单编号": "SO-1", "客户编号": "C-1", "金额": 150},
            {"订单编号": "SO-2", "客户编号": "C-2", "金额": 200},
            {"订单编号": "SO-3", "客户编号": "C-3", "金额": 300},
        ],
    )
    return orders


async def test_a_changed_row_names_the_fields_that_changed(db: AsyncSession) -> None:
    """A row that changed is one row with a field list, not a row of noise."""
    orders = await _two_versions(db)

    result = await compare.compare(db, orders, from_version=1, to_version=2)

    changed = [row for row in result.rows if row.change == "changed"]
    assert [(row.record_key, row.changed_fields) for row in changed] == [(_key("SO-1"), ["金额"])]
    assert changed[0].before["金额"] == 100
    assert changed[0].after["金额"] == 150


async def test_additions_come_first_and_truncation_says_so(db: AsyncSession) -> None:
    """Somebody comparing versions is usually looking for what arrived."""
    orders = await _two_versions(db)

    result = await compare.compare(db, orders, from_version=1, to_version=2)
    assert (result.added, result.changed, result.unchanged) == (1, 1, 1)

    truncated = await compare.compare(db, orders, from_version=1, to_version=2, limit=1)
    assert truncated.truncated is True
    assert [row.change for row in truncated.rows] == ["changed"]


async def test_a_deleted_record_is_removed_and_says_so(db: AsyncSession) -> None:
    """The tombstone is the difference between `removed` and `missing`."""
    orders = await _two_versions(db)
    await maintenance.delete_row(
        db, orders, record_key_value=_key("SO-2"), reason="duplicate", actor_user_id=None
    )

    result = await compare.compare(db, orders, from_version=2, to_version=3)

    assert result.removed == 1
    assert result.missing == 0
    assert [row.change for row in result.rows] == ["removed"]


async def test_an_unpublished_version_cannot_be_compared(db: AsyncSession) -> None:
    orders = await _two_versions(db)

    with pytest.raises(BadRequestError):
        await compare.compare(db, orders, from_version=1, to_version=9)


async def test_comparing_a_version_with_itself_is_empty_not_an_error(
    db: AsyncSession,
) -> None:
    """A mistake a person can make by picking the same version twice."""
    orders = await _two_versions(db)

    result = await compare.compare(db, orders, from_version=2, to_version=2)

    assert (result.rows, result.added, result.changed, result.removed) == ([], 0, 0, 0)
