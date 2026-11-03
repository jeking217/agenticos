"""The wizard's steps, saved so closing the dialog loses nothing (T07).

Two claims, both about the same thing - that a half-made decision is not a
publish:

- saving a draft keeps the choices and the step reached, and writes no rows and
  no versions;
- reading the import back returns what was saved, so the dialog can put itself
  where the person left it, and the commit carries those same choices into a
  version.

Real Postgres and a real `.xlsx`: the plan the wizard resumes from is the one
`excel.scan` produced, and a saved choice about a sheet the workbook does not
have is refused rather than remembered.
"""

import uuid
from io import BytesIO
from typing import Any

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.db.models.knowledge_base import KBScope, KnowledgeBase
from app.db.models.organization import Organization
from app.db.models.user import User
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import (
    DatasetFieldInput,
    DatasetImportDraft,
    DatasetImportSheet,
)
from app.services.kb_dataset import excel, imports
from app.services.kb_dataset.model import create_dataset

pytestmark = pytest.mark.anyio

ORDERS = "订单"
NOTES = "说明"


def _workbook() -> bytes:
    book = Workbook()
    orders = book.active
    orders.title = ORDERS
    orders.append(["订单编号", "金额"])
    orders.append(["SO-1", 100])
    notes = book.create_sheet(NOTES)
    notes.append(["备注"])
    notes.append(["第一次导入"])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def _base(db: AsyncSession) -> KnowledgeBase:
    owner = User(email=f"{uuid.uuid4()}@example.com", hashed_password="x", is_active=True)
    db.add(owner)
    await db.flush()
    organization = Organization(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}", created_by_user_id=owner.id
    )
    db.add(organization)
    await db.flush()
    base = KnowledgeBase(
        id=uuid.uuid4(),
        name=f"orders-{uuid.uuid4().hex[:8]}",
        scope=KBScope.ORG.value,
        collection_name=f"orders-{uuid.uuid4().hex[:8]}",
        kb_type="dataset",
        embedding_model="text-embedding-3-small",
        embedding_dim=1536,
        embedding_provider="openrouter",
        organization_id=organization.id,
        owner_user_id=owner.id,
        visibility="org",
    )
    db.add(base)
    await db.flush()
    return base


async def _staged(db: AsyncSession, base: KnowledgeBase, path: Any) -> Any:
    content = _workbook()
    path.write_bytes(content)
    record = await repo.create_import(
        db,
        knowledge_base_id=base.id,
        organization_id=base.organization_id,
        status="preview",
        filename="订单.xlsx",
        file_size=len(content),
        file_digest="0" * 64,
        sheet_count=2,
        plan={
            "filename": "订单.xlsx",
            "step": "model",
            "sheets": [imports.proposal_payload(sheet) for sheet in excel.scan(content)],
        },
        summary={},
        created_by=None,
    )
    record.staged_path = str(path)
    await db.flush()
    return record


async def _dataset(db: AsyncSession, base: KnowledgeBase) -> Any:
    return await create_dataset(
        db,
        knowledge_base_id=base.id,
        organization_id=base.organization_id,
        name="订单明细",
        fields=[
            DatasetFieldInput(name="订单编号", data_type="text", is_primary_key=True),
            DatasetFieldInput(name="金额", data_type="integer"),
        ],
    )


async def test_a_draft_keeps_the_step_and_the_choices_and_publishes_nothing(
    db: AsyncSession, tmp_path: Any
) -> None:
    base = await _base(db)
    record = await _staged(db, base, tmp_path / "orders.xlsx")
    dataset = await _dataset(db, base)

    plan = await imports.save_draft(
        db,
        record=record,
        draft=DatasetImportDraft(
            step="validate",
            sheets=[
                DatasetImportSheet(
                    sheet_name=ORDERS,
                    dataset_id=dataset.id,
                    primary_key_fields=["订单编号"],
                    on_invalid="skip",
                )
            ],
            note="三月对账",
        ),
    )

    assert plan["step"] == "validate"
    assert plan["choices"]["step"] == "validate"
    assert plan["choices"]["sheets"][0]["on_invalid"] == "skip"
    # The proposals are still there: the next screen shows the same workbook.
    assert [sheet["sheet_name"] for sheet in plan["sheets"]] == [ORDERS, NOTES]
    # Nothing was published: no version and no rows came out of a draft.
    await db.refresh(dataset)
    assert dataset.current_version_no == 0
    assert (await repo.list_versions(db, dataset.id))[0] == []


async def test_the_saved_draft_is_what_reading_it_back_returns(
    db: AsyncSession, tmp_path: Any
) -> None:
    """The resume path is one read: open the import, get the step and choices."""
    base = await _base(db)
    record = await _staged(db, base, tmp_path / "orders.xlsx")
    dataset = await _dataset(db, base)

    await imports.save_draft(
        db,
        record=record,
        draft=DatasetImportDraft(
            step="model",
            sheets=[DatasetImportSheet(sheet_name=ORDERS, dataset_id=dataset.id)],
        ),
    )

    plan = imports.plan_of(record)

    assert plan["filename"] == "订单.xlsx"
    assert plan["step"] == "model"
    assert [sheet["sheet_name"] for sheet in plan["choices"]["sheets"]] == [ORDERS]
    assert plan["choices"]["sheets"][0]["dataset_id"] == str(dataset.id)


async def test_a_choice_about_a_sheet_the_workbook_lacks_is_refused(
    db: AsyncSession, tmp_path: Any
) -> None:
    """A stale tab must not save a decision the commit could then act on."""
    base = await _base(db)
    record = await _staged(db, base, tmp_path / "orders.xlsx")

    with pytest.raises(BadRequestError):
        await imports.save_draft(
            db,
            record=record,
            draft=DatasetImportDraft(sheets=[DatasetImportSheet(sheet_name="不存在")]),
        )


async def test_a_draft_cannot_be_saved_over_a_published_import(
    db: AsyncSession, tmp_path: Any
) -> None:
    """Once versions exist, the choices describe something that already happened."""
    base = await _base(db)
    record = await _staged(db, base, tmp_path / "orders.xlsx")
    record.status = "committed"

    with pytest.raises(BadRequestError):
        await imports.save_draft(db, record=record, draft=DatasetImportDraft())


async def test_the_contract_a_saved_draft_is_readable_as_its_own_model(
    db: AsyncSession, tmp_path: Any
) -> None:
    """`plan` travels as JSON and comes back as `DatasetImportPlan` on the wire."""
    from app.schemas.kb_dataset import DatasetImportPlan

    base = await _base(db)
    record = await _staged(db, base, tmp_path / "orders.xlsx")
    dataset = await _dataset(db, base)

    await imports.save_draft(
        db,
        record=record,
        draft=DatasetImportDraft(
            step="sheets",
            sheets=[DatasetImportSheet(sheet_name=ORDERS, dataset_id=dataset.id)],
        ),
    )

    plan = DatasetImportPlan.model_validate(imports.plan_of(record))

    assert plan.step == "sheets"
    assert plan.choices is not None
    assert plan.choices.sheets[0].dataset_id == dataset.id
