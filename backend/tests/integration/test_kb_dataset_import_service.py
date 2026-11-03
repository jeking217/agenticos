"""The import wizard's half of `DatasetService` (T29).

The parser, the wizard's draft and the publishing are tested on their own. What
this file covers is the join between them and the request: the upload becomes an
import record, the record is what the dialog re-opens, the commit either waits
for the publish or hands it to a worker, and every one of those is scoped to the
knowledge base it was asked in.

The four verbs here are the four a wizard makes, in order:

    preview_import -> save_import_draft -> queue_import | commit_import
                                          -> import_status / imports

Real Postgres and a real `.xlsx`, because the thing that can go wrong is a
status: an import that says `preview` while a version exists is a dialog that
publishes twice.
"""

import uuid
from io import BytesIO
from typing import Any

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, BadRequestError, NotFoundError
from app.core.permissions import AuthContext
from app.db.models.audit_log import AppAdminAuditLog
from app.db.models.background_job import BackgroundJob
from app.db.models.knowledge_base import KnowledgeBase
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import (
    DatasetImportCommit,
    DatasetImportDraft,
    DatasetImportSheet,
)
from app.services.kb_dataset import DatasetService
from tests.integration.kb_dataset_support import base

pytestmark = pytest.mark.anyio

SHEET = "订单"
FILENAME = "订单.xlsx"


def _workbook() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(["订单编号", "金额"])
    sheet.append(["SO-1", 100])
    book.save(buffer := BytesIO())
    return buffer.getvalue()


def _ctx(kb: KnowledgeBase) -> AuthContext:
    return AuthContext(user_id=kb.owner_user_id, organization_id=kb.organization_id, role="owner")


async def _previewed(db: AsyncSession) -> tuple[KnowledgeBase, AuthContext, Any]:
    kb = await base(db)
    ctx = _ctx(kb)
    record = await DatasetService(db).preview_import(
        kb.id, ctx, filename=FILENAME, content=_workbook()
    )
    return kb, ctx, record


def _commit(*, name: str = "订单明细") -> DatasetImportCommit:
    return DatasetImportCommit(
        sheets=[
            DatasetImportSheet(sheet_name=SHEET, dataset_name=name, primary_key_fields=["订单编号"])
        ]
    )


# -- the upload -------------------------------------------------------------


async def test_a_preview_parses_the_workbook_and_publishes_nothing(
    db: AsyncSession,
) -> None:
    """The first step of the wizard is a proposal, not data: no dataset, no rows."""
    kb, _, record = await _previewed(db)

    assert record.status == "preview"
    assert record.sheet_count == 1
    assert (record.plan or {})["step"] == "model"
    assert [sheet["sheet_name"] for sheet in record.plan["sheets"]] == [SHEET]
    assert await repo.get_dataset_by_kb(db, kb.id) == []


async def test_reading_the_import_back_gives_the_wizard_its_place(
    db: AsyncSession,
) -> None:
    """One read puts the dialog back on the step it was left on, with the plan."""
    kb, ctx, record = await _previewed(db)

    status = await DatasetService(db).import_status(kb.id, record.id, ctx)

    assert status["status"] == "preview"
    assert status["filename"] == FILENAME
    assert status["plan"]["step"] == "model"
    assert status["progress"]["percent"] == 0
    assert status["committed_at"] is None


async def test_a_saved_draft_is_what_reopening_the_import_returns(
    db: AsyncSession,
) -> None:
    """Closing the dialog at step 3 of 6 costs the choices, not the upload."""
    kb, ctx, record = await _previewed(db)
    service = DatasetService(db)

    saved = await service.save_import_draft(
        kb.id,
        record.id,
        ctx,
        DatasetImportDraft(
            step="validate",
            sheets=[DatasetImportSheet(sheet_name=SHEET, on_invalid="skip")],
            note="三月对账",
        ),
    )

    assert saved["choices"]["step"] == "validate"
    status = await service.import_status(kb.id, record.id, ctx)
    assert status["plan"]["choices"]["sheets"][0]["on_invalid"] == "skip"
    assert status["plan"]["choices"]["note"] == "三月对账"
    # A draft is not a publish.
    assert await repo.get_dataset_by_kb(db, kb.id) == []


async def test_the_imports_list_is_what_the_history_panel_renders(
    db: AsyncSession,
) -> None:
    kb, ctx, record = await _previewed(db)

    listing = await DatasetService(db).imports(kb.id, ctx, skip=0, limit=10)

    assert listing["total"] == 1
    (item,) = listing["items"]
    assert item["id"] == record.id
    assert item["filename"] == FILENAME
    assert [sheet["sheet_name"] for sheet in item["sheets"]] == [SHEET]


# -- the two ways to publish ------------------------------------------------


async def test_a_commit_publishes_here_and_now_and_records_what_it_did(
    db: AsyncSession,
) -> None:
    """The small-workbook path: the caller waited, and there is a version."""
    kb, ctx, record = await _previewed(db)

    result = await DatasetService(db).commit_import(kb.id, record.id, ctx, _commit())

    assert result["status"] == "committed"
    assert result["sheets"][0]["rows_added"] == 1
    (created,) = await repo.get_dataset_by_kb(db, kb.id)
    assert (created.name, created.current_version_no, created.row_count) == ("订单明细", 1, 1)
    refreshed = await repo.get_import(db, record.id)
    assert refreshed.status == "committed"
    assert refreshed.committed_at is not None
    # The staged workbook is gone: a finished import does not keep a copy.
    assert refreshed.staged_path is None
    audit = (
        await db.execute(
            select(AppAdminAuditLog).where(AppAdminAuditLog.action == "dataset.import_committed")
        )
    ).scalars()
    assert [entry.details["filename"] for entry in audit] == [FILENAME]


async def test_a_queued_import_answers_before_the_workbook_is_read(
    db: AsyncSession,
) -> None:
    """A 40 MB file takes minutes; a request does not hold a connection for it."""
    kb, ctx, record = await _previewed(db)

    accepted = await DatasetService(db).queue_import(kb.id, record.id, ctx, _commit())

    assert accepted["status"] == "queued"
    assert accepted["import_id"] == record.id
    jobs = list(
        (
            await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "import-dataset"))
        ).scalars()
    )
    assert len(jobs) == 1
    # Nothing is published by accepting it.
    assert await repo.get_dataset_by_kb(db, kb.id) == []


async def test_an_import_that_was_already_published_cannot_be_queued_again(
    db: AsyncSession,
) -> None:
    """A second click is one publish, not two versions of the same numbers."""
    kb, ctx, record = await _previewed(db)
    service = DatasetService(db)
    await service.commit_import(kb.id, record.id, ctx, _commit())

    with pytest.raises(BadRequestError):
        await service.queue_import(kb.id, record.id, ctx, _commit())


# -- scope ------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["status", "draft", "queue", "commit"])
async def test_an_import_from_another_knowledge_base_is_not_found(
    db: AsyncSession, verb: str
) -> None:
    """The import id alone is not a way in: the base the caller holds is."""
    _, _, record = await _previewed(db)
    other = await base(db, name="others")
    service = DatasetService(db)
    ctx = _ctx(other)

    with pytest.raises(NotFoundError):
        match verb:
            case "status":
                await service.import_status(other.id, record.id, ctx)
            case "draft":
                await service.save_import_draft(other.id, record.id, ctx, DatasetImportDraft())
            case "queue":
                await service.queue_import(other.id, record.id, ctx, _commit())
            case _:
                await service.commit_import(other.id, record.id, ctx, _commit())


async def test_an_operator_that_does_not_own_the_base_cannot_commit_into_it(
    db: AsyncSession,
) -> None:
    """Writing is guarded like reading: another tenant's import is a 404, not a 403
    with a hint about what exists."""
    kb, _, record = await _previewed(db)
    stranger = AuthContext(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="owner")

    with pytest.raises((NotFoundError, AuthorizationError)):
        await DatasetService(db).commit_import(kb.id, record.id, stranger, _commit())
