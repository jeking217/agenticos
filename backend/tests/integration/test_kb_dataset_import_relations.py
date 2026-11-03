"""What the model adds to an import, and what publishing does with it (T17).

Two halves, both about the same promise: the advice is a suggestion and the
joins it suggests are created only after there is something to join.

- `analyze_import` on a base with no model answers `ran: false` with a reason
  rather than refusing - an import is never blocked by its advice - and the
  answer is kept, so reopening the dialog does not buy a second call.
- `commit_import` with accepted joins creates them once the sheets are datasets,
  and a join naming something that failed to publish is a line in the report
  rather than a failed import.

Real Postgres and a real `.xlsx`: the thing that can go wrong here is ordering -
a join created before its dataset exists has nothing to point at.
"""

from io import BytesIO
from typing import Any

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import AuthContext
from app.db.models.knowledge_base import KnowledgeBase
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import (
    DatasetImportCommit,
    DatasetImportRelation,
    DatasetImportSheet,
)
from app.services.kb_dataset import DatasetService
from tests.integration.kb_dataset_support import base

pytestmark = pytest.mark.anyio

ORDERS = "订单"
LINES = "订单明细"


def _workbook() -> bytes:
    """Two sheets that join on the order number, and one that joins nothing."""
    book = Workbook()
    orders = book.active
    orders.title = ORDERS
    orders.append(["订单编号", "金额"])
    orders.append(["SO-1", 100])
    orders.append(["SO-2", 200])

    lines = book.create_sheet(LINES)
    lines.append(["行号", "订单编号", "商品"])
    lines.append([1, "SO-1", "纸箱"])
    lines.append([2, "SO-2", "胶带"])

    cover = book.create_sheet("说明")
    cover.append(["这一页用来测试汇总表"])
    cover.append(["没有可导入的数据"])

    book.save(buffer := BytesIO())
    return buffer.getvalue()


def _ctx(kb: KnowledgeBase) -> AuthContext:
    return AuthContext(user_id=kb.owner_user_id, organization_id=kb.organization_id, role="owner")


async def _previewed(db: AsyncSession) -> tuple[KnowledgeBase, AuthContext, Any]:
    kb = await base(db)
    ctx = _ctx(kb)
    record = await DatasetService(db).preview_import(
        kb.id, ctx, filename="报价.xlsx", content=_workbook()
    )
    return kb, ctx, record


def _commit(*relations: DatasetImportRelation) -> DatasetImportCommit:
    return DatasetImportCommit(
        sheets=[
            DatasetImportSheet(sheet_name=ORDERS, dataset_name=ORDERS, primary_key_fields=["订单编号"]),
            DatasetImportSheet(sheet_name=LINES, dataset_name=LINES, primary_key_fields=["行号"]),
        ],
        relations=list(relations),
    )


# -- the advice -------------------------------------------------------------


async def test_a_base_with_no_model_says_so_instead_of_refusing(db: AsyncSession) -> None:
    """An import is not blocked by its advice, so a missing model is an answer."""
    kb, ctx, record = await _previewed(db)

    answer = await DatasetService(db).analyze_import(kb.id, record.id, ctx)

    assert answer["ran"] is False
    assert answer["reason"] == "no_model"
    # The parser's own proposal, so the wizard always has something to show.
    assert [sheet["sheet_name"] for sheet in answer["sheets"]] == [ORDERS, LINES, "说明"]


async def test_the_advice_is_kept_so_reopening_does_not_ask_twice(db: AsyncSession) -> None:
    kb, ctx, record = await _previewed(db)
    service = DatasetService(db)
    await service.analyze_import(kb.id, record.id, ctx)

    again = await service.analyze_import(kb.id, record.id, ctx)

    assert again["cached"] is True


# -- the joins --------------------------------------------------------------


async def test_a_join_the_wizard_kept_is_created_once_the_sheets_are(db: AsyncSession) -> None:
    kb, ctx, record = await _previewed(db)

    result = await DatasetService(db).commit_import(
        kb.id,
        record.id,
        ctx,
        _commit(
            DatasetImportRelation(
                from_sheet=LINES,
                from_fields=["订单编号"],
                to_sheet=ORDERS,
                to_fields=["订单编号"],
                cardinality="n:1",
            )
        ),
    )

    created = result["relations"]
    assert len(created) == 1
    assert created[0]["created"] is True

    datasets = {dataset.name: dataset for dataset in await repo.get_dataset_by_kb(db, kb.id)}
    relations = await repo.list_relations(db, datasets[LINES].id)

    assert [(relation.name, relation.status) for relation in relations] == [(f"to_{ORDERS}", "confirmed")]


async def test_a_join_naming_a_sheet_that_did_not_publish_is_reported(db: AsyncSession) -> None:
    """The import stands; the join that has nowhere to land is a line in the report."""
    kb, ctx, record = await _previewed(db)

    result = await DatasetService(db).commit_import(
        kb.id,
        record.id,
        ctx,
        _commit(
            DatasetImportRelation(
                from_sheet=LINES,
                from_fields=["订单编号"],
                to_sheet="库存",  # not in the workbook and not a dataset
                to_fields=["单号"],
            )
        ),
    )

    assert result["status"] == "committed"
    assert result["relations"][0]["created"] is False
    assert "库存" in result["relations"][0]["error"]


async def test_a_join_into_an_existing_dataset_is_allowed(db: AsyncSession) -> None:
    """A second workbook extends a dataset made by the first, and can join it."""
    kb, ctx, record = await _previewed(db)
    service = DatasetService(db)
    await service.commit_import(
        kb.id,
        record.id,
        ctx,
        DatasetImportCommit(
            sheets=[
                DatasetImportSheet(
                    sheet_name=ORDERS, dataset_name=ORDERS, primary_key_fields=["订单编号"]
                )
            ]
        ),
    )
    second = await service.preview_import(
        kb.id, ctx, filename="补录.xlsx", content=_workbook()
    )

    result = await service.commit_import(
        kb.id,
        second.id,
        ctx,
        _commit(
            DatasetImportRelation(
                from_sheet=LINES,
                from_fields=["订单编号"],
                to_sheet=ORDERS,  # an existing dataset, not a sheet of this workbook
                to_fields=["订单编号"],
            )
        ),
    )

    assert result["relations"][0]["created"] is True


def test_the_relation_schema_refuses_two_sides_of_different_widths() -> None:
    with pytest.raises(ValueError):
        DatasetImportRelation(
            from_sheet=ORDERS, from_fields=["订单编号", "金额"], to_sheet=LINES, to_fields=["行号"]
        )
