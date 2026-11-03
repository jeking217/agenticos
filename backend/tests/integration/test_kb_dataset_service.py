"""`DatasetService` - the one door every route and every agent tool goes through (T29).

The routes are thin and the compiler is tested on its own; what is left is the
layer between them, and it has one job per method: resolve the caller's access
through the knowledge base *first*, then answer in the shape a client binds to.

Two things are asserted here and nowhere else:

- **the shapes on the wire** - a version list that marks the current one and the
  pinned one, a hand edit that names the version it made, a relation that knows
  the name of the dataset it points at. A client compiles against these.
- **the facts beside the version** - an edit is a row in `dataset_operations`
  *and* an audit entry that carries the reason. The values are deliberately not
  copied into the audit trail (retention does not reach it), so what the trail
  has to hold is who, which record, which version, and why.

Real Postgres, because the answers are SQL: a mocked repository would only
assert that the mock was called.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.permissions import AuthContext
from app.db.models.audit_log import AppAdminAuditLog
from app.db.models.knowledge_base import KnowledgeBase
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import (
    DatasetBaseCreate,
    DatasetCreate,
    DatasetFieldChange,
    DatasetFieldInput,
    DatasetRelationInput,
    DatasetUpdate,
)
from app.services.kb_dataset import DatasetService, versions
from tests.integration.kb_dataset_support import ORDERS, base, dataset, published

pytestmark = pytest.mark.anyio

ROWS = [
    {"订单编号": "SO-1", "客户编号": "C-1", "金额": 100},
    {"订单编号": "SO-2", "客户编号": "C-2", "金额": 200},
]

CUSTOMERS = [
    DatasetFieldInput(name="客户编号", data_type="text", is_primary_key=True),
    DatasetFieldInput(name="客户名称", data_type="text"),
]


def _ctx(kb: KnowledgeBase) -> AuthContext:
    return AuthContext(user_id=kb.owner_user_id, organization_id=kb.organization_id, role="owner")


def _key(number: str) -> str:
    """The stored identity of an order number - normalised, so not `SO-1`."""
    return versions.record_key({"订单编号": number}, ["订单编号"])


async def _seeded(db: AsyncSession) -> tuple[KnowledgeBase, Any, AuthContext]:
    kb = await base(db)
    orders = await dataset(db, kb)
    await published(db, orders, ROWS)
    await db.refresh(orders)
    return kb, orders, _ctx(kb)


# -- bases ------------------------------------------------------------------


async def test_a_created_base_holds_rows_and_has_no_collection_behind_it(
    db: AsyncSession,
) -> None:
    """The embedding columns are a sentinel, not a model: nothing is vectorised.

    The columns are NOT NULL for every other base, so leaving them empty is not
    an option - and a base that named a real model would be one `ingestion`
    could try to embed.
    """
    kb = await base(db)

    created = await DatasetService(db).create_base(
        DatasetBaseCreate(name="销售数据", scope="org"), _ctx(kb)
    )

    assert created.kb_type == "dataset"
    assert (created.embedding_model, created.embedding_dim, created.embedding_provider) == (
        "none",
        1,
        "none",
    )
    assert created.collection_name.startswith("ds-")
    assert created.organization_id == kb.organization_id


async def test_a_personal_base_without_an_organization_is_refused(
    db: AsyncSession,
) -> None:
    """A personal base is somebody's own; without an owner there is no "whose"."""
    kb = await base(db)
    anonymous = AuthContext(user_id=kb.owner_user_id, organization_id=None, role="owner")

    with pytest.raises(BadRequestError):
        await DatasetService(db).create_base(DatasetBaseCreate(name="我的数据"), anonymous)


async def test_the_listing_leaves_out_what_was_archived(db: AsyncSession) -> None:
    kb = await base(db)
    await dataset(db, kb, name="保留")
    gone = await dataset(db, kb, name="归档")
    await repo.archive_dataset(db, gone.id, datetime.now(UTC))

    names = [item.name for item in await DatasetService(db).list_datasets(kb.id, _ctx(kb))]

    assert names == ["保留"]


# -- the dataset itself -----------------------------------------------------


async def test_creating_a_dataset_agrees_the_model_and_publishes_nothing(
    db: AsyncSession,
) -> None:
    """Rows come later; the contract the importer and the compiler read comes now."""
    kb = await base(db)

    read = await DatasetService(db).create(
        kb.id,
        DatasetCreate(knowledge_base_id=kb.id, name="订单明细", fields=list(ORDERS)),
        _ctx(kb),
    )

    assert read.current_version_no == 0
    assert read.row_count == 0
    assert [field.name for field in read.fields] == ["订单编号", "客户编号", "金额"]
    assert read.primary_key_fields == ["订单编号"]


async def test_renaming_onto_a_taken_name_is_refused(db: AsyncSession) -> None:
    """Two datasets with one name would make the name unusable as an identity."""
    kb = await base(db)
    await dataset(db, kb, name="甲")
    second = await dataset(db, kb, name="乙")
    service = DatasetService(db)

    with pytest.raises(BadRequestError):
        await service.rename(second.id, DatasetUpdate(name="甲"), _ctx(kb))

    assert (await service.get_dataset(second.id, _ctx(kb))).name == "乙"


async def test_a_renamed_dataset_reads_back_under_its_new_name(
    db: AsyncSession,
) -> None:
    """The answer is the whole row, timestamps included.

    `updated_at` is written by the database, so the attribute is expired after
    the flush and reading it is IO. The first version of this test failed with
    `MissingGreenlet`: `PATCH /kb/datasets/{id}` was a 500 rather than a rename.
    """
    kb = await base(db)
    orders = await dataset(db, kb, name="甲")

    read = await DatasetService(db).rename(orders.id, DatasetUpdate(name="甲-改"), _ctx(kb))

    assert read.name == "甲-改"
    assert read.updated_at is not None
    assert (await repo.get_dataset(db, orders.id)).name == "甲-改"


async def test_replacing_the_model_bumps_its_version_once(db: AsyncSession) -> None:
    """A model version is the history of the *shape*: what a field meant when a
    number was computed. Adding a column is one new shape, not one per field."""
    kb, orders, ctx = await _seeded(db)

    read = await DatasetService(db).update_model(
        orders.id,
        DatasetFieldChange(fields=[*ORDERS, DatasetFieldInput(name="备注", data_type="text")]),
        ctx,
    )

    assert read.model_version == 2
    assert [field.name for field in read.fields] == ["订单编号", "客户编号", "金额", "备注"]
    # The rows are untouched: a model change is not a data version.
    assert read.current_version_no == 1


# -- rows and versions ------------------------------------------------------


async def test_reading_a_version_that_was_never_published_is_refused(
    db: AsyncSession,
) -> None:
    """`version=99` is a typo or a guess, and neither is an empty page."""
    _, orders, ctx = await _seeded(db)

    with pytest.raises(BadRequestError):
        await DatasetService(db).list_rows(
            orders.id, ctx, version_no=99, search=None, skip=0, limit=10
        )


async def test_the_version_list_marks_the_current_one_and_the_pinned_one(
    db: AsyncSession,
) -> None:
    kb, orders, ctx = await _seeded(db)
    service = DatasetService(db)
    await service.pin(orders.id, ctx, version_no=1, tool_call_id=uuid.uuid4())

    listing = await service.versions(orders.id, ctx, skip=0, limit=10)

    (item,) = listing["items"]
    assert listing["total"] == 1
    assert (item["version_no"], item["current"], item["pinned"]) == (1, True, True)


async def test_the_model_as_of_a_version_is_readable(db: AsyncSession) -> None:
    """The shape is versioned too, or a 2023 answer cannot be re-read in 2025."""
    _, orders, ctx = await _seeded(db)

    model = await DatasetService(db).version_snapshot(orders.id, ctx, 1)

    assert model["version_no"] == 1
    assert [field["name"] for field in model["fields"]] == ["订单编号", "客户编号", "金额"]


async def test_a_comparison_names_the_rows_and_the_fields_that_moved(
    db: AsyncSession,
) -> None:
    """`changed: 2` is a count; a reviewer needs the row and the column."""
    _, orders, ctx = await _seeded(db)
    await published(
        db,
        orders,
        [{"订单编号": "SO-1", "客户编号": "C-1", "金额": 150}, ROWS[1]],
    )
    await db.refresh(orders)

    result = await DatasetService(db).compare(
        orders.id, ctx, from_version=1, to_version=2, limit=200
    )

    assert (result.from_version, result.to_version) == (1, 2)
    assert (result.added, result.changed, result.removed, result.unchanged) == (0, 1, 0, 1)
    (change,) = result.rows
    assert change.record_key == _key("SO-1")
    assert change.changed_fields == ["金额"]
    assert (change.before or {})["金额"] == 100
    assert (change.after or {})["金额"] == 150


async def test_a_row_that_was_never_there_has_no_snapshot(db: AsyncSession) -> None:
    _, orders, ctx = await _seeded(db)

    with pytest.raises(NotFoundError):
        await DatasetService(db).row_snapshot(orders.id, ctx, record_key=_key("SO-9"), version_no=1)


# -- maintenance, and the trail beside it -----------------------------------


async def _audit(db: AsyncSession, action: str) -> list[AppAdminAuditLog]:
    rows = await db.execute(select(AppAdminAuditLog).where(AppAdminAuditLog.action == action))
    return list(rows.scalars().all())


async def test_a_hand_edit_names_the_version_it_made_and_the_reason(
    db: AsyncSession,
) -> None:
    """The operations list has the values; the audit trail has the "why"."""
    _, orders, ctx = await _seeded(db)
    service = DatasetService(db)

    result = await service.update_row(
        orders.id,
        ctx,
        record_key=_key("SO-1"),
        values={"金额": 150},
        reason="客户更正",
        expected_version=None,
    )

    assert result == {"version_no": 2, "record_key": _key("SO-1")}
    operations = await service.operations(orders.id, ctx, skip=0, limit=10)
    assert operations["total"] == 1
    assert operations["items"][0]["operation"] == "update"
    assert operations["items"][0]["reason"] == "客户更正"
    (entry,) = await _audit(db, "dataset.row_updated")
    assert entry.target_id == str(orders.id)
    assert entry.details["record_key"] == _key("SO-1")
    assert entry.details["reason"] == "客户更正"
    assert entry.details["version_no"] == 2


async def test_an_insert_is_audited_under_the_key_it_was_given(
    db: AsyncSession,
) -> None:
    """An insert has no `record_key` argument to echo, so the trail derives it
    from the values - a row nobody can name is a row nobody can be asked about."""
    _, orders, ctx = await _seeded(db)

    result = await DatasetService(db).insert_row(
        orders.id,
        ctx,
        values={"订单编号": "SO-3", "客户编号": "C-3", "金额": 300},
        reason="电话订单",
        expected_version=None,
    )

    assert result["version_no"] == 2
    (entry,) = await _audit(db, "dataset.row_inserted")
    assert entry.details["record_key"] == "SO-3"


async def test_deleting_and_restoring_are_two_versions_and_two_entries(
    db: AsyncSession,
) -> None:
    _, orders, ctx = await _seeded(db)
    service = DatasetService(db)

    deleted = await service.delete_row(
        orders.id, ctx, record_key=_key("SO-2"), reason="重复", expected_version=None
    )
    restored = await service.restore_row(
        orders.id, ctx, record_key=_key("SO-2"), reason="并非重复", expected_version=None
    )

    assert (deleted["version_no"], restored["version_no"]) == (2, 3)
    # Both entries are written through the same transaction as the version, so
    # their `created_at` is the same instant and the order is not asserted.
    assert {entry.details["record_key"] for entry in await _audit(db, "dataset.row_deleted")} == {
        _key("SO-2")
    }
    assert {entry.details["reason"] for entry in await _audit(db, "dataset.row_restored")} == {
        "并非重复"
    }
    operations = await service.operations(orders.id, ctx, skip=0, limit=10)
    assert operations["total"] == 2
    assert {item["operation"] for item in operations["items"]} == {"delete", "restore"}


# -- relations --------------------------------------------------------------


async def _two_datasets(db: AsyncSession) -> tuple[KnowledgeBase, Any, Any]:
    kb = await base(db)
    orders = await dataset(db, kb, name="订单明细")
    customers = await dataset(db, kb, name="客户", fields=list(CUSTOMERS))
    await published(db, orders, ROWS)
    await published(
        db,
        customers,
        [{"客户编号": "C-1", "客户名称": "甲"}, {"客户编号": "C-2", "客户名称": "乙"}],
        key=("客户编号",),
    )
    await db.refresh(orders)
    await db.refresh(customers)
    return kb, orders, customers


async def test_a_relation_knows_the_name_of_what_it_points_at(
    db: AsyncSession,
) -> None:
    """The canvas draws an edge by name, and an id is not something to draw."""
    kb, orders, customers = await _two_datasets(db)
    service = DatasetService(db)

    created, warnings = await service.create_relation(
        orders.id,
        _ctx(kb),
        DatasetRelationInput(
            target_dataset_id=customers.id,
            name="客户表",
            local_fields=["客户编号"],
            foreign_fields=["客户编号"],
        ),
    )

    assert warnings == []
    (listed,) = await service.relations(orders.id, _ctx(kb))
    assert listed["target_dataset_name"] == "客户"
    assert listed["local_fields"] == ["客户编号"]
    (entry,) = await _audit(db, "dataset.relation_created")
    assert entry.target_id == str(created["id"])


async def test_a_relation_can_be_removed_once(db: AsyncSession) -> None:
    kb, orders, customers = await _two_datasets(db)
    service = DatasetService(db)
    created, _ = await service.create_relation(
        orders.id,
        _ctx(kb),
        DatasetRelationInput(
            target_dataset_id=customers.id,
            name="客户表",
            local_fields=["客户编号"],
            foreign_fields=["客户编号"],
        ),
    )

    await service.delete_relation(orders.id, created["id"], _ctx(kb))

    assert await service.relations(orders.id, _ctx(kb)) == []
    with pytest.raises(NotFoundError):
        await service.delete_relation(orders.id, created["id"], _ctx(kb))


async def test_candidates_are_offered_from_the_datasets_beside_it(
    db: AsyncSession,
) -> None:
    """A suggestion is read from the data: the names line up and so do the values."""
    kb, orders, _ = await _two_datasets(db)

    candidates = await DatasetService(db).relation_candidates(orders.id, _ctx(kb))

    (best,) = candidates
    assert best["target_dataset_name"] == "客户"
    assert best["local_fields"] == ["客户编号"]
    assert best["confidence"] >= 0.45


# -- archive and lookup -----------------------------------------------------


async def test_archiving_takes_the_dataset_out_of_the_listing_and_keeps_it(
    db: AsyncSession,
) -> None:
    """Archiving is not a delete: the rows, the versions and the audit stay."""
    kb, orders, ctx = await _seeded(db)
    service = DatasetService(db)

    await service.archive(orders.id, ctx)

    assert await service.list_datasets(kb.id, ctx) == []
    archived = await repo.get_dataset(db, orders.id)
    assert archived is not None
    assert archived.archived_at is not None
    assert archived.current_version_no == 1
    with pytest.raises(NotFoundError):
        await service.get_dataset(orders.id, ctx)
    (entry,) = await _audit(db, "dataset.archived")
    assert entry.details["row_count"] == 2


async def test_a_dataset_can_be_named_or_identified_by_id(db: AsyncSession) -> None:
    """The agent's tool layer holds whatever the model wrote: a name it read from
    a listing, or an id it carried over from an earlier turn."""
    kb, orders, ctx = await _seeded(db)
    service = DatasetService(db)

    by_name = await service.resolve_by_name(kb.id, "订单明细", ctx)
    by_id = await service.resolve_by_name(kb.id, str(orders.id), ctx)

    assert by_name.id == orders.id
    assert by_id.id == orders.id


async def test_a_name_from_another_knowledge_base_is_not_found(
    db: AsyncSession,
) -> None:
    """Resolving is scoped to the base the question was asked in: a dataset with
    the same name elsewhere is a different dataset, not a hit."""
    kb, orders, ctx = await _seeded(db)
    other = await base(db, name="others")

    with pytest.raises(NotFoundError):
        await DatasetService(db).resolve_by_name(other.id, "订单明细", ctx)
    with pytest.raises(NotFoundError):
        await DatasetService(db).resolve_by_name(kb.id, "SO-1", ctx)
    with pytest.raises(NotFoundError):
        await DatasetService(db).resolve_by_name(kb.id, str(uuid.uuid4()), ctx)
