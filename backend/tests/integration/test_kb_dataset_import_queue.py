"""An import the API accepts and a worker publishes (T09).

Three claims, each one only true if the pieces are wired together:

- accepting a commit answers before the workbook is read, and the job it queues
  is the one that publishes it - not a second code path that happens to agree;
- the poller can name the sheet in flight, and counts a failed sheet as settled,
  because "3 of 5 done, one failed" is what a person needs to act on;
- a worker that dies between two sheets leaves the first one published and the
  rest for the retry, and the retry does not publish the first one twice - which
  is what "进程中断可恢复" has to mean to be worth claiming.

Real Postgres, real `.xlsx`, and the worker's own entry point.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Any

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.db.models.audit_log import AppAdminAuditLog
from app.db.models.background_job import BackgroundJob
from app.db.models.knowledge_base import KBScope, KnowledgeBase
from app.db.models.organization import Organization
from app.db.models.user import User
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import (
    DatasetFieldInput,
    DatasetImportCommit,
    DatasetImportSheet,
)
from app.services.kb_dataset import excel, imports
from app.services.kb_dataset.model import create_dataset
from app.worker import jobs
from app.worker.tasks import dataset_tasks

pytestmark = pytest.mark.anyio

ORDERS = "订单"
ORDERS_TOO = "订单补充"


def _workbook(*, second_sheet: bool = False) -> bytes:
    """One sheet, or two that go into the same dataset.

    A second dataset would need a second knowledge base - a dataset *is* one,
    and the table says so with a unique constraint - so the second sheet here is
    more orders, which is also the more interesting case for a retry: the rows
    that already published must not be published again.
    """
    book = Workbook()
    orders = book.active
    orders.title = ORDERS
    orders.append(["订单编号", "金额"])
    orders.append(["SO-1", 100])
    orders.append(["SO-2", 200])
    if second_sheet:
        more = book.create_sheet(ORDERS_TOO)
        more.append(["订单编号", "金额"])
        more.append(["SO-3", 300])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def _base(db: AsyncSession) -> tuple[KnowledgeBase, uuid.UUID]:
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
    return base, organization.id


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


async def _staged(
    db: AsyncSession, base: KnowledgeBase, path: Any, *, second_sheet: bool = False
) -> Any:
    """An import record whose staged file is the workbook above.

    `imports.preview` is skipped - it writes into the deployment's media
    directory, which this suite does not own. What is under test starts at the
    commit, and the plan it reads is the one preview would have written.
    """
    content = _workbook(second_sheet=second_sheet)
    path.write_bytes(content)
    record = await repo.create_import(
        db,
        knowledge_base_id=base.id,
        organization_id=base.organization_id,
        status="preview",
        filename="订单.xlsx",
        file_size=len(content),
        file_digest="0" * 64,
        sheet_count=2 if second_sheet else 1,
        plan={"sheets": [imports.proposal_payload(sheet) for sheet in excel.scan(content)]},
        summary={},
        created_by=None,
    )
    record.staged_path = str(path)
    await db.flush()
    return record


def _commit(*datasets: tuple[str, Any]) -> DatasetImportCommit:
    return DatasetImportCommit(
        sheets=[
            DatasetImportSheet(sheet_name=sheet, dataset_id=dataset.id)
            for sheet, dataset in datasets
        ]
    )


def _queued(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the worker at this test's session instead of a NullPool engine."""

    @asynccontextmanager
    async def same_session() -> AsyncIterator[AsyncSession]:
        yield db

    monkeypatch.setattr(dataset_tasks, "get_worker_db_context", same_session)


async def _job_row(db: AsyncSession) -> BackgroundJob:
    rows = (
        await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "import-dataset"))
    ).scalars()
    jobs_ = list(rows)
    assert len(jobs_) == 1
    return jobs_[0]


async def test_an_accepted_import_is_published_by_the_worker(
    db: AsyncSession, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _queued(db, monkeypatch)
    base, organization_id = await _base(db)
    dataset = await _dataset(db, base)
    record = await _staged(db, base, tmp_path / "orders.xlsx")

    answer = await imports.accept(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=organization_id,
        payload=_commit((ORDERS, dataset)),
        actor_user_id=None,
    )
    await db.commit()

    # Answered as accepted, with the row already saying so.
    assert answer["status"] == "queued"
    assert answer["progress"]["percent"] == 0
    assert record.status == "queued"

    job = jobs.IMPORT_DATASET.load((await _job_row(db)).payload)
    result = await dataset_tasks.run_import(job)

    assert result["status"] == "committed"
    await db.refresh(record)
    assert record.status == "committed"
    assert imports.progress_of(record) == {
        "phase": "done",
        "total": 1,
        "done": 1,
        "failed": 0,
        "current_sheet": None,
        "percent": 100,
    }
    await db.refresh(dataset)
    assert dataset.current_version_no == 1
    assert len((await repo.list_versions(db, dataset.id))[0]) == 1

    # The write happened on a worker, and the trail still says who asked and
    # what it replaced - the audit is the worker's, not the request's.
    audit = (
        await db.execute(
            select(AppAdminAuditLog).where(AppAdminAuditLog.action == "dataset.import_committed")
        )
    ).scalar_one()
    assert audit.details["import_id"] == str(record.id)
    assert audit.details["sheets"][0]["version_no"] == 1

    # The staged workbook is released: nothing is left waiting on disk.
    assert record.staged_path is None


async def test_accepting_twice_queues_one_publish(db: AsyncSession, tmp_path: Any) -> None:
    """A double-click is a second accept, and must not publish the same rows twice."""
    base, organization_id = await _base(db)
    dataset = await _dataset(db, base)
    record = await _staged(db, base, tmp_path / "orders.xlsx")
    payload = _commit((ORDERS, dataset))

    await imports.accept(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=organization_id,
        payload=payload,
        actor_user_id=None,
    )
    with pytest.raises(BadRequestError):
        await imports.accept(
            db,
            record=record,
            knowledge_base_id=base.id,
            organization_id=organization_id,
            payload=payload,
            actor_user_id=None,
        )

    assert len(list((await db.execute(select(BackgroundJob))).scalars())) == 1


async def test_progress_names_the_sheet_in_flight(db: AsyncSession, tmp_path: Any) -> None:
    """Before the work and after it: a bar alone cannot say which sheet is slow."""
    base, organization_id = await _base(db)
    dataset = await _dataset(db, base)
    record = await _staged(db, base, tmp_path / "orders.xlsx")
    seen: list[tuple[str | None, int, str]] = []

    class Recorded(imports.SavedProgress):
        async def report(self, record: Any, summary: dict[str, Any]) -> None:
            seen.append((summary["current_sheet"], summary["done"], summary["phase"]))
            await super().report(record, summary)

    await imports.commit(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=organization_id,
        payload=_commit((ORDERS, dataset)),
        actor_user_id=None,
        progress=Recorded(db),
    )

    assert seen == [
        (None, 0, "running"),  # accepted, nothing started
        (ORDERS, 0, "running"),  # in flight, named
        (None, 1, "running"),  # landed
        (None, 1, "done"),
    ]


async def test_a_worker_that_dies_between_sheets_leaves_the_first_published(
    db: AsyncSession, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery means the retry finishes the rest and does not redo what landed."""
    _queued(db, monkeypatch)
    base, organization_id = await _base(db)
    orders = await _dataset(db, base)
    record = await _staged(db, base, tmp_path / "two.xlsx", second_sheet=True)
    payload = _commit((ORDERS, orders), (ORDERS_TOO, orders))

    await imports.accept(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=organization_id,
        payload=payload,
        actor_user_id=None,
    )
    await db.commit()
    job = jobs.IMPORT_DATASET.load((await _job_row(db)).payload)

    class Dies(imports.SavedProgress):
        """Killed the moment the second sheet is announced: the shape of an
        out-of-memory kill, which is when a workbook is most likely to do it."""

        async def report(self, record: Any, summary: dict[str, Any]) -> None:
            if summary["current_sheet"] == ORDERS_TOO:
                raise RuntimeError("worker killed mid-import")
            await super().report(record, summary)

    record.status = imports.RUNNING
    await db.commit()
    with pytest.raises(RuntimeError):
        await imports.commit(
            db,
            record=record,
            knowledge_base_id=base.id,
            organization_id=organization_id,
            payload=payload,
            actor_user_id=None,
            progress=Dies(db),
        )
    await db.rollback()

    await db.refresh(orders)
    assert orders.current_version_no == 1

    # What the scheduler does when the job's lease expires: nothing can finish it,
    # so it is closed - and `failed` is a status the person can publish from again.
    await dataset_tasks.settle_lost_import(job, "lost its lease")
    await db.refresh(record)
    assert record.status == "failed"
    assert "lost its lease" in record.error
    assert record.staged_path is not None

    result = await dataset_tasks.run_import(job)

    assert result["status"] == "committed"
    await db.refresh(orders)
    # Two versions, not three: the sheet the first run already published changed
    # nothing, so it minted nothing.
    assert orders.current_version_no == 2
    assert len((await repo.list_versions(db, orders.id))[0]) == 2


async def test_a_failed_sheet_does_not_hold_the_bar_below_a_hundred(
    db: AsyncSession, tmp_path: Any
) -> None:
    """The report says `partial`; the progress says that nothing is still running."""
    base, organization_id = await _base(db)
    dataset = await _dataset(db, base)
    record = await _staged(db, base, tmp_path / "orders.xlsx")

    result = await imports.commit(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=organization_id,
        payload=DatasetImportCommit(
            sheets=[
                DatasetImportSheet(sheet_name=ORDERS, dataset_id=dataset.id),
                DatasetImportSheet(sheet_name="不存在的表", dataset_id=dataset.id),
            ]
        ),
        actor_user_id=None,
    )

    assert result["status"] == "partial"
    progress = imports.progress_of(record)
    assert progress["percent"] == 100
    assert (progress["done"], progress["failed"]) == (2, 1)


async def test_the_workbook_is_opened_once_per_import(
    db: AsyncSession, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One workbook, one parse - not one per sheet.

    `rows_of` re-reads the whole file for the sheet it was asked about, so a
    six-sheet workbook used to be parsed six times to publish six sheets: the
    cost of an import grew with the number of sheets, which is the one thing it
    should not do when the sheets come from one file. Measured, not suspected -
    `tests/integration/test_kb_dataset_capacity.py` is the run that found it.

    Counted at the loader rather than timed: a timing assertion on a parse is a
    machine-speed assertion, and what must not change is how many times the file
    is read.
    """
    base, organization_id = await _base(db)
    dataset = await _dataset(db, base)
    content = _workbook(second_sheet=True)
    record = await _staged(db, base, tmp_path / "orders.xlsx", second_sheet=True)

    opened: list[str] = []
    real = excel._load_xlsx

    def counted(data: bytes) -> Any:
        opened.append("opened")
        return real(data)

    monkeypatch.setattr(excel, "_load_xlsx", counted)

    result = await imports.commit(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=organization_id,
        payload=DatasetImportCommit(
            sheets=[
                DatasetImportSheet(sheet_name=ORDERS, dataset_id=dataset.id),
                DatasetImportSheet(sheet_name=ORDERS_TOO, dataset_id=dataset.id),
            ]
        ),
        actor_user_id=None,
    )

    assert result["status"] == "committed"
    assert [sheet["status"] for sheet in result["sheets"]] == ["committed", "committed"]
    assert len(opened) == 1, f"the workbook was parsed {len(opened)} times for two sheets"
    assert len(content) > 0


# -- what a worker does when it cannot publish -------------------------------
#
# The happy path is above. These are the three ways a worker stops being able to
# finish, and each one leaves the row in a different place: the import is gone,
# it was already published, or the publish raised. The last two are the ones a
# person sees, because the poller renders whatever the row says.


async def _accepted(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> tuple[Any, Any, Any]:
    """A base, a dataset and an accepted (queued) import of the workbook above."""
    _queued(db, monkeypatch)
    base, organization_id = await _base(db)
    dataset = await _dataset(db, base)
    record = await _staged(db, base, tmp_path / "orders.xlsx")
    payload = _commit((ORDERS, dataset))
    await imports.accept(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=organization_id,
        payload=payload,
        actor_user_id=None,
    )
    await db.commit()
    job = imports.job_payload(
        record,
        knowledge_base_id=base.id,
        organization_id=organization_id,
        payload=payload,
        actor_user_id=None,
    )
    return record, dataset, job


async def test_a_job_whose_import_is_gone_answers_missing_rather_than_raising(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A rollback or a prune can take the row out from under a queued job. The
    answer is what the panel shows, and an exception there is a red job with no
    explanation."""
    _, _, job = await _accepted(db, monkeypatch, tmp_path)

    answer = await dataset_tasks.run_import(job.model_copy(update={"import_id": uuid.uuid4()}))

    assert answer == {"status": "missing"}


async def test_a_job_for_an_import_that_was_already_published_mints_nothing(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The first attempt reached its last write and died before the job row
    caught up. Publishing again could only mint a version for a no-op."""
    record, dataset, job = await _accepted(db, monkeypatch, tmp_path)
    assert (await dataset_tasks.run_import(job))["status"] == "committed"

    answer = await dataset_tasks.run_import(job)

    assert answer == {"status": "committed"}
    await db.refresh(dataset)
    assert dataset.current_version_no == 1


async def test_a_publish_that_raises_leaves_the_import_retryable(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The refusals all happen before the first sheet, so nothing was published -
    and the row says why instead of sitting on `running` forever."""
    record, dataset, job = await _accepted(db, monkeypatch, tmp_path)
    (tmp_path / "orders.xlsx").unlink()

    with pytest.raises(NotFoundError):
        await dataset_tasks.run_import(job)

    await db.refresh(record)
    assert record.status == "failed"
    assert "no longer available" in (record.error or "")
    await db.refresh(dataset)
    assert dataset.current_version_no == 0


async def test_a_worker_that_died_leaves_the_import_publishable_again(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The staged workbook is still there and the row is still `running`; nothing
    but this closes it, and the poller cannot tell slow from abandoned."""
    record, _, job = await _accepted(db, monkeypatch, tmp_path)

    await dataset_tasks.settle_lost_import(job, "no heartbeat for 30 minutes")

    await db.refresh(record)
    assert record.status == "failed"
    assert "no heartbeat" in (record.error or "")
    assert record.summary["phase"] == "done"
    # `failed` is a status `commit` accepts, so the recovery path is the button
    # the person already has - and the workbook it needs is still on disk.
    assert await imports.accept(
        db,
        record=record,
        knowledge_base_id=job.knowledge_base_id,
        organization_id=job.organization_id,
        payload=imports.commit_spec(job),
        actor_user_id=None,
    )
