"""An import that a worker publishes, and the progress a poller reads (T09).

The unit under test is the handover: what the job carries, what the row says
while it is out, and what a poller is allowed to conclude from it. The
publishing itself is `commit`, tested where it runs.
"""

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.exceptions import BadRequestError
from app.schemas.kb_dataset import DatasetImportCommit, DatasetImportSheet
from app.services.kb_dataset import imports
from app.worker import jobs

pytestmark = pytest.mark.anyio


def _commit(**overrides: Any) -> DatasetImportCommit:
    sheet = {
        "sheet_name": "订单",
        "dataset_id": uuid.uuid4(),
        "on_invalid": "skip",
        "primary_key_fields": ["订单编号"],
    }
    sheet.update(overrides)
    return DatasetImportCommit(sheets=[DatasetImportSheet(**sheet)], note="三月对账")


def _record(tmp_path: Path | None = None, status: str = "preview") -> SimpleNamespace:
    staged: str | None = None
    if tmp_path is not None:
        path = tmp_path / "orders.xlsx"
        path.write_bytes(b"staged")
        staged = str(path)
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        filename="orders.xlsx",
        staged_path=staged,
        summary={},
    )


def _job(commit: DatasetImportCommit) -> jobs.ImportDataset:
    return imports.job_payload(
        _record(),
        knowledge_base_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        payload=commit,
        actor_user_id=uuid.uuid4(),
    )


def test_the_job_carries_every_decision_the_wizard_made() -> None:
    """A sheet's target, its policy and the note survive the queue as they were.

    The payload is written in its own terms rather than the request's model, so
    this is the only place that would notice the two drifting apart.
    """
    commit = _commit()
    job = _job(commit)

    assert imports.commit_spec(job).model_dump() == commit.model_dump()


def test_an_import_runs_on_the_pool_that_parses() -> None:
    """Not the agent pool and not the sweeps': a workbook is CPU and memory."""
    task = jobs.TASKS[jobs.IMPORT_DATASET.kind]

    assert task.pool is jobs.Pool.INGEST
    assert task.max_attempts == 2
    # A cancelled job would leave the import row `running` with nothing to
    # finish it, and no message to the person waiting on it.
    assert task.cancellable is False


def test_the_ingest_child_knows_the_kind() -> None:
    """A pooled child loads only `handlers.ingest()`, so the kind must be there."""
    from app.worker import handlers

    assert jobs.IMPORT_DATASET.kind in handlers.ingest()


def test_progress_counts_a_failed_sheet_as_settled() -> None:
    """The bar reaching 100% while saying one failed is the honest reading.

    A sheet that failed is done being tried; counting only successes would leave
    a workbook of five with a bar stuck at 80% forever.
    """
    record = SimpleNamespace(
        status="partial",
        summary={"total": 5, "done": 5, "failed": 1, "sheets": [{}, {}, {}, {}, {}]},
    )

    progress = imports.progress_of(record)

    assert progress == {
        "phase": "done",
        "total": 5,
        "done": 5,
        "failed": 1,
        "current_sheet": None,
        "percent": 100,
    }


def test_a_record_from_before_the_queue_is_not_shown_as_never_started() -> None:
    """Imports written by the earlier release have no `total` in their summary.

    Answering 0% for a finished import would read as a stall, so a settled one
    reports the sheets it recorded instead.
    """
    record = SimpleNamespace(status="committed", summary={"sheets": [{}, {}], "failures": 0})

    assert imports.progress_of(record)["percent"] == 100
    assert imports.progress_of(record)["done"] == 2


def test_the_sheet_in_flight_is_named_while_it_runs() -> None:
    record = SimpleNamespace(
        status="running",
        summary={"total": 4, "done": 1, "failed": 0, "current_sheet": "客户", "sheets": [{}]},
    )

    progress = imports.progress_of(record)

    assert (progress["phase"], progress["percent"], progress["current_sheet"]) == (
        "running",
        25,
        "客户",
    )


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_second_commit_is_refused_while_the_first_is_out(tmp_path: Path, status: str) -> None:
    """Two accepts would publish the same workbook twice and mint a version for
    a no-op on every sheet."""
    record = _record(tmp_path, status=status)

    with pytest.raises(BadRequestError):
        imports._refuse_a_repeat(record)


def test_an_import_already_committed_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BadRequestError):
        imports._refuse_a_repeat(_record(tmp_path, status="committed"))


def test_a_workbook_that_is_gone_is_a_refusal_not_a_queued_job() -> None:
    """Otherwise the job fails a minute later, after a worker took it."""
    from app.core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        imports._refuse_a_repeat(_record(status="preview"))


async def test_accept_writes_queued_and_queues_one_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record(tmp_path)
    queued: list[tuple[Any, Any]] = []

    async def enqueue(_db: Any, task: Any, payload: Any, **options: Any) -> uuid.UUID:
        queued.append((task, payload, options))
        return uuid.uuid4()

    monkeypatch.setattr(imports, "enqueue", enqueue)

    answer = await imports.accept(
        None,  # type: ignore[arg-type]
        record=record,
        knowledge_base_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        payload=_commit(),
        actor_user_id=None,
    )

    assert record.status == "queued"
    assert answer["status"] == "queued"
    assert answer["progress"]["percent"] == 0

    [entry] = queued
    task, payload, options = entry
    assert task.kind == "import-dataset"
    assert payload.import_id == record.id
    # One publish per import, however many times the button is pressed.
    assert options["dedupe_key"] == f"dataset-import:{record.id}"
    assert options["dedupe_active"] is True


async def test_saved_progress_commits_each_report() -> None:
    """A poller on another connection cannot see an uncommitted summary."""

    class Db:
        commits = 0

        async def commit(self) -> None:
            Db.commits += 1

    record = SimpleNamespace(status="running", summary={})
    summary = {"total": 2, "done": 1}

    await imports.SavedProgress(Db()).report(record, summary)  # type: ignore[arg-type]

    assert Db.commits == 1
    assert record.summary == summary


async def test_giving_up_leaves_it_publishable_again() -> None:
    """`failed` is a status `commit` accepts: the retry is the button that exists."""
    record = SimpleNamespace(
        status="running",
        error=None,
        summary={"total": 3, "done": 1, "failed": 0, "sheets": [{"sheet_name": "订单"}]},
    )

    class Db:
        commits = 0

        async def commit(self) -> None:
            Db.commits += 1

    await imports.give_up(Db(), record, "worker stopped")  # type: ignore[arg-type]

    assert record.status == "failed"
    assert "worker stopped" in record.error
    assert record.summary["total"] == 3
    # What already landed is still reported: the retry only has to do the rest.
    assert record.summary["sheets"] == [{"sheet_name": "订单"}]
    assert Db.commits == 1
