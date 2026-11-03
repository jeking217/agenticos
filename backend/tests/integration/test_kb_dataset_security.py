"""Three ways into somebody else's data, and where each one stops (T32).

The review behind this file asked three questions and the tests are the answers:

1. **Tenant isolation** - a second organization's dataset is not readable, and
   not merely unlisted: the service refuses it by id as well, with the same
   answer a missing row gives, so the refusal cannot be used to probe for what
   exists.
2. **Reading history sideways** - an old version is not a back door around the
   permission that guards the current one. A member who may not read the
   dataset cannot read what it said last month either, and a personal base is
   closed to the organization that shares a workspace with it.
3. **The injection surface** - a field name is a key in a JSON document and an
   alias in a result, never an identifier in a statement; a value is always
   bound. The tests below attack both and assert what comes out.

They live here rather than beside the feature because they are one artefact:
the load-bearing assumption of every other test is that the tenant boundary
holds.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.permissions import AuthContext
from app.db.models.knowledge_base import KBScope
from app.schemas.kb_dataset import DatasetQuery
from app.services.kb_dataset import DatasetService, query as compiler

from tests.integration.kb_dataset_support import ORDERS, base, dataset, published

pytestmark = pytest.mark.anyio

ROWS = [
    {"订单编号": "SO-1", "客户编号": "C-1", "金额": 100},
    {"订单编号": "SO-2", "客户编号": "C-2", "金额": 200},
]


def _ctx(organization_id: uuid.UUID, *, user_id: uuid.UUID | None = None, role: str) -> AuthContext:
    return AuthContext(
        user_id=user_id or uuid.uuid4(), organization_id=organization_id, role=role
    )


async def _own(db: AsyncSession) -> tuple[Any, AuthContext, Any]:
    kb = await base(db)
    orders = await dataset(db, kb, fields=list(ORDERS))
    await published(db, orders, ROWS)
    await db.refresh(orders)
    return orders, _ctx(kb.organization_id, user_id=kb.owner_user_id, role="owner"), kb


# -- 1. tenant isolation ----------------------------------------------------


async def test_another_organization_cannot_read_a_dataset_by_id(db: AsyncSession) -> None:
    """Refused by id, not only hidden from a list: guessing the id gets nowhere."""
    orders, _, _ = await _own(db)
    stranger = _ctx(uuid.uuid4(), role="owner")

    with pytest.raises((NotFoundError, AuthorizationError)):
        await DatasetService(db).dataset(orders.id, stranger)


async def test_another_organization_cannot_query_it_either(db: AsyncSession) -> None:
    """The refusal is in front of the compiler, so no statement is ever built."""
    orders, _, _ = await _own(db)
    stranger = _ctx(uuid.uuid4(), role="owner")

    with pytest.raises((NotFoundError, AuthorizationError)):
        await DatasetService(db).query(
            orders.id, stranger, DatasetQuery(dataset=orders.name, fields=[{"field": "金额"}])
        )


async def test_a_member_of_the_same_organization_can_read_it(db: AsyncSession) -> None:
    """The guard is the organization, not the door: same tenant, same data."""
    orders, _, kb = await _own(db)
    colleague = _ctx(kb.organization_id, role="member")

    read = await DatasetService(db).get_dataset(orders.id, colleague)

    assert read.id == orders.id


# -- 2. history is not a side door ------------------------------------------


async def test_history_is_closed_to_whoever_may_not_read_the_dataset(
    db: AsyncSession,
) -> None:
    """Reading last month's version needs the same access as reading today's."""
    orders, _, _ = await _own(db)
    stranger = _ctx(uuid.uuid4(), role="owner")

    with pytest.raises((NotFoundError, AuthorizationError)):
        await DatasetService(db).version_snapshot(orders.id, stranger, 1)


async def test_a_personal_base_is_closed_to_the_rest_of_the_organization(
    db: AsyncSession,
) -> None:
    """A base in somebody's own space is not shared by being in the same org."""
    kb = await base(db)
    kb.scope = KBScope.PERSONAL.value
    kb.visibility = "private"
    await db.flush()
    orders = await dataset(db, kb, fields=list(ORDERS))
    await published(db, orders, ROWS)
    colleague = _ctx(kb.organization_id, role="member")

    with pytest.raises((NotFoundError, AuthorizationError)):
        await DatasetService(db).get_dataset(orders.id, colleague)


async def test_another_tenant_cannot_pin_or_compare_against_a_version(
    db: AsyncSession,
) -> None:
    """Every verb that touches a version is guarded, not only the read ones."""
    orders, _, _ = await _own(db)
    stranger = _ctx(uuid.uuid4(), role="owner")
    service = DatasetService(db)

    with pytest.raises((NotFoundError, AuthorizationError)):
        await service.compare(orders.id, stranger, from_version=1, to_version=2, limit=200)
    with pytest.raises((NotFoundError, AuthorizationError)):
        await service.pin(orders.id, stranger, version_no=1)


# -- 3. the injection surface ------------------------------------------------


def _context() -> Any:
    """The smallest model the compiler accepts: one text field to filter on."""
    from app.db.models.kb_dataset import Dataset, DatasetField  # noqa: PLC0415
    from app.services.kb_dataset.query import QueryContext  # noqa: PLC0415

    orders = Dataset()
    orders.id = uuid.uuid4()
    orders.name = "订单"
    orders.storage_partition_key = uuid.uuid4()
    orders.current_version_no = 1
    field = DatasetField()
    field.name = "订单编号"
    field.data_type = "text"
    return QueryContext(
        dataset=orders,
        fields={"订单编号": field},
        metrics={},
        relations={},
        version_no=1,
    )


def test_a_field_name_cannot_be_a_statement_fragment() -> None:
    """`"`, `'` and control characters are refused before anything is compiled:
    a field name is a JSON key and a result alias, and both are quoted in SQL."""
    from pydantic import ValidationError

    from app.schemas.kb_dataset import DatasetFieldInput

    for name in ['金额"; DROP TABLE dataset_rows; --', "金额'", "  ", "金额\x00"]:
        with pytest.raises(ValidationError):
            DatasetFieldInput(name=name, data_type="text")


def test_surrounding_whitespace_is_normalised_rather_than_stored() -> None:
    """A header with a trailing newline is the same field, not a second one -
    the schema strips before it validates, so `金额\n` cannot become a twin of
    `金额` that no query can tell apart from it."""
    from app.schemas.kb_dataset import DatasetFieldInput

    assert DatasetFieldInput(name=" 金额\n", data_type="text").name == "金额"


def test_a_value_is_bound_and_never_spelled_into_the_statement() -> None:
    """The hostile string travels as a parameter, so it is data - not syntax."""
    hostile = "'; DROP TABLE dataset_rows; --"
    compiled = compiler.compile_query(
        _context(),
        DatasetQuery(
            dataset="订单",
            fields=[{"field": "订单编号"}],
            filters={"field": "订单编号", "op": "eq", "value": hostile},
        ),
    )

    assert hostile not in compiled.sql
    assert hostile in compiled.params.values()


def test_a_join_must_name_a_relation_that_was_registered() -> None:
    """The compiler reads its FROM and JOIN clauses from the model, so an
    unregistered relation is a refusal rather than a table nobody expected."""
    from app.core.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        compiler.compile_query(
            _context(),
            DatasetQuery(
                dataset="订单",
                fields=[{"field": "订单编号"}],
                joins=[{"relation": "客户表"}],
            ),
        )
