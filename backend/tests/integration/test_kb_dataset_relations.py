"""Relations between two datasets of one base: what may be stored, and what is
only a suggestion (T34/T35).

A relation is a promise the compiler relies on - these two fields mean the same
thing, so a join on them does not multiply rows into nonsense. That is why the
tests are about refusals as much as about the happy path: a wrong promise is
worse than no join at all, because the answer it produces is plausible.
"""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, BadRequestError
from app.schemas.kb_dataset import DatasetFieldInput, DatasetRelationInput
from app.services.kb_dataset import relations

from tests.integration.kb_dataset_support import ORDERS, base, dataset, published

pytestmark = pytest.mark.anyio

CUSTOMERS = [
    DatasetFieldInput(name="客户编号", data_type="text", is_primary_key=True),
    DatasetFieldInput(name="客户名称", data_type="text"),
    DatasetFieldInput(name="信用额度", data_type="numeric"),
]


async def _pair(db: AsyncSession) -> tuple[Any, Any]:
    """Two datasets in one base, with values that line up on `客户编号`."""
    kb = await base(db)
    orders = await dataset(db, kb, name="订单明细", fields=list(ORDERS))
    customers = await dataset(db, kb, name="客户档案", fields=list(CUSTOMERS))
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
        customers,
        [
            {"客户编号": "C-1", "客户名称": "甲公司", "信用额度": 5000},
            {"客户编号": "C-2", "客户名称": "乙公司", "信用额度": 8000},
        ],
        key=("客户编号",),
    )
    return orders, customers


def _join(customers: Any, **overrides: Any) -> DatasetRelationInput:
    payload = {
        "target_dataset_id": customers.id,
        "name": "客户表",
        "local_fields": ["客户编号"],
        "foreign_fields": ["客户编号"],
    }
    payload.update(overrides)
    return DatasetRelationInput(**payload)


async def test_a_relation_whose_values_line_up_is_stored_as_confirmed(
    db: AsyncSession,
) -> None:
    orders, customers = await _pair(db)

    relation, warnings = await relations.create(db, orders, _join(customers))

    assert relation.status == "confirmed"
    assert warnings == []
    assert relation.local_fields == ["客户编号"]


async def test_two_different_kinds_of_value_are_refused(db: AsyncSession) -> None:
    """An integer joined to text compares a number with a name and matches nothing."""
    orders, customers = await _pair(db)

    with pytest.raises(BadRequestError):
        await relations.create(
            db,
            orders,
            _join(customers, local_fields=["金额"], foreign_fields=["客户名称"]),
        )


async def test_a_dataset_cannot_be_joined_to_itself(db: AsyncSession) -> None:
    orders, _ = await _pair(db)

    with pytest.raises(BadRequestError):
        await relations.create(
            db,
            orders,
            _join(orders, local_fields=["客户编号"], foreign_fields=["客户编号"]),
        )


async def test_a_relation_to_another_base_is_refused(db: AsyncSession) -> None:
    """A join across tenants is the one mistake that would leak data, so the
    relation cannot even be stored."""
    orders, _ = await _pair(db)
    other = await base(db, name="other")
    stranger = await dataset(db, other, name="客户档案", fields=list(CUSTOMERS))

    with pytest.raises(BadRequestError):
        await relations.create(db, orders, _join(stranger))


async def test_a_second_relation_with_the_same_name_is_refused(db: AsyncSession) -> None:
    orders, customers = await _pair(db)
    await relations.create(db, orders, _join(customers))

    with pytest.raises(AlreadyExistsError):
        await relations.create(db, orders, _join(customers))


async def test_values_that_barely_overlap_are_a_warning_not_a_refusal(
    db: AsyncSession,
) -> None:
    """A wrong guess is worth saying out loud, but a new dataset has no rows yet
    and overlaps with nothing - refusing that would make the wizard unusable.

    Four customers and one shared id out of them: a quarter of the smaller side,
    under the threshold, so the join is stored with the doubt attached.
    """
    kb = await base(db)
    orders = await dataset(db, kb, name="订单明细", fields=list(ORDERS))
    customers = await dataset(db, kb, name="客户档案", fields=list(CUSTOMERS))
    await published(
        db,
        orders,
        [
            {"订单编号": f"SO-{n}", "客户编号": "C-1" if n == 1 else f"Z-{n}", "金额": n}
            for n in range(1, 21)
        ],
    )
    await published(
        db,
        customers,
        [{"客户编号": f"C-{n}", "客户名称": f"公司{n}", "信用额度": 1000} for n in range(1, 5)],
        key=("客户编号",),
    )

    _, warnings = await relations.create(db, orders, _join(customers))

    assert warnings and "客户编号" in warnings[0]


async def test_a_candidate_is_suggested_from_names_and_values(db: AsyncSession) -> None:
    """Nothing is stored: a suggestion is a dashed line until somebody confirms."""
    orders, customers = await _pair(db)

    [candidate] = await relations.infer_candidates(db, orders)

    assert candidate["target_dataset_id"] == str(customers.id)
    assert candidate["local_fields"] == ["客户编号"]
    assert candidate["already_exists"] is False
    assert candidate["confidence"] >= 0.45
    # Nothing was written by looking.
    assert await relations.repo.list_relations(db, orders.id) == []


async def test_a_confirmed_relation_is_reported_as_already_there(
    db: AsyncSession,
) -> None:
    """The wizard draws a solid line only where a relation actually exists.

    Confirmed under the name the suggestion offered, which is the way a person
    accepts a dashed line rather than renaming it.
    """
    orders, customers = await _pair(db)
    [suggested] = await relations.infer_candidates(db, orders)
    await relations.create(db, orders, _join(customers, name=suggested["name"]))

    [candidate] = await relations.infer_candidates(db, orders)

    assert candidate["already_exists"] is True


async def test_overlap_is_measured_against_the_smaller_side(db: AsyncSession) -> None:
    """A customers table with two rows and 200 orders is still a 100% match."""
    orders, customers = await _pair(db)

    ratio = await relations.overlap_ratio(
        db,
        orders,
        (await relations.repo.list_fields(db, orders.id))[1],
        customers,
        (await relations.repo.list_fields(db, customers.id))[0],
    )

    assert ratio == 1.0


async def test_listing_a_field_s_values_is_refused_for_a_field_that_is_not_there(
    db: AsyncSession,
) -> None:
    orders, _ = await _pair(db)

    with pytest.raises(BadRequestError):
        await relations.distinct_values_for_ui(db, orders, "利润")
