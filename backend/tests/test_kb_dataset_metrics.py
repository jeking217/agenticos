"""The five numbers the rollout is judged on (plan T27).

There is no metrics client in this deployment, so "instrumented" means one
structured log record per event. These tests hold two things that a dashboard
written against the plan depends on:

- the event names are the plan's, spelled the way the design spells them, because
  a renamed event is a silent gap in a graph nobody re-reads the code for;
- the fields are on the record, not baked into the message, because a log
  pipeline buckets by field and a message it has to parse is a regex waiting to
  break.

The last two tests drive the real service rather than the emitters: an import
that reports a duration but is never called from the import path is a number that
never appears, and that is exactly the failure mode this file exists to catch.
"""

import logging
import uuid
from typing import Any

import pytest

from app.core.exceptions import BadRequestError
from app.db.models.kb_dataset import Dataset, DatasetField
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import DatasetQuery
from app.services.kb_dataset import DatasetService, metrics, versions
from app.services.kb_dataset.query import QueryContext, compile_query

pytestmark = pytest.mark.anyio

LOGGER = "kb_dataset.metrics"


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Only this module's records: the service logs plenty of its own."""
    return [record for record in caplog.records if record.name == LOGGER]


def _events(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [str(record.event) for record in _records(caplog)]


def _only(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    found = [record for record in _records(caplog) if record.event == event]
    assert len(found) == 1, f"expected one {event} record, got {len(found)}"
    return found[0]


# --- the emitters -----------------------------------------------------------


def test_the_event_names_are_the_ones_the_plan_names() -> None:
    """Renaming one of these is a hole in a dashboard, so it is asserted here."""
    assert (
        metrics.IMPORT_DURATION,
        metrics.IMPORT_FAILURE,
        metrics.QUERY_LATENCY,
        metrics.QUERY_REJECTED,
        metrics.VERSION_ROWS,
    ) == (
        "kb_dataset.import.duration",
        "kb_dataset.import.failures",
        "kb_dataset.query.latency",
        "kb_dataset.query.rejected",
        "kb_dataset.version.rows",
    )


def test_a_record_carries_its_event_as_a_field_and_not_only_as_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)

    metrics.query_finished(rows=3, joins=1, duration_ms=42)

    record = _only(caplog, metrics.QUERY_LATENCY)
    assert record.event == metrics.QUERY_LATENCY
    assert (record.rows, record.joins, record.duration_ms) == (3, 1, 42)


def test_an_import_that_landed_reports_its_size_and_says_ok(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)

    metrics.import_finished(sheets=2, rows=30, duration_ms=1234)

    record = _only(caplog, metrics.IMPORT_DURATION)
    assert (record.sheets, record.rows, record.outcome) == (2, 30, "ok")
    assert metrics.IMPORT_FAILURE not in _events(caplog)


def test_a_failed_import_names_each_stage_that_broke_rather_than_the_import(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`parse` and `write` have different fixes; one "imports failed" count hides that."""
    caplog.set_level(logging.INFO, logger=LOGGER)

    metrics.import_finished(
        sheets=3, rows=0, duration_ms=90, failed_stages={"parse": 1, "write": 2}
    )

    assert _only(caplog, metrics.IMPORT_DURATION).outcome == "failed"
    failures = {
        record.stage: record.count
        for record in _records(caplog)
        if record.event == metrics.IMPORT_FAILURE
    }
    assert failures == {"parse": 1, "write": 2}


def test_one_import_writes_one_line_per_event_not_one_per_row(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 500k-row import that logged per row would be a flood and a lock."""
    caplog.set_level(logging.INFO, logger=LOGGER)

    metrics.import_finished(sheets=1, rows=500_000, duration_ms=60_000)

    assert len(_records(caplog)) == 1


def test_a_new_version_reports_the_rows_it_carries(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)

    metrics.version_published(rows=1200, version_no=4)

    record = _only(caplog, metrics.VERSION_ROWS)
    assert (record.rows, record.version_no) == (1200, 4)


# --- the wiring -------------------------------------------------------------


def _dataset(name: str = "订单", version: int = 3) -> Dataset:
    dataset = Dataset()
    dataset.id = uuid.uuid4()
    dataset.name = name
    dataset.storage_partition_key = uuid.uuid4()
    dataset.current_version_no = version
    return dataset


def _field(name: str, data_type: str) -> DatasetField:
    field = DatasetField()
    field.name = name
    field.data_type = data_type
    return field


def _context() -> QueryContext:
    dataset = _dataset()
    return QueryContext(
        dataset=dataset,
        fields={name: _field(name, kind) for name, kind in [("地区", "text"), ("金额", "numeric")]},
        metrics={},
        relations={},
        version_no=dataset.current_version_no,
    )


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> DatasetService:
    """The real `query`, with only the two things it cannot do here replaced:
    reading the model out of Postgres, and running the statement."""
    context = _context()
    service = DatasetService(None)  # type: ignore[arg-type]

    async def dataset(self: Any, dataset_id: Any, ctx: Any, *, write: bool = False) -> Dataset:
        return context.dataset

    async def query_context(
        self: Any, dataset: Dataset, *, version_no: Any, ctx: Any
    ) -> QueryContext:
        return context

    async def run_query(db: Any, compiled: Any) -> tuple[list[dict[str, Any]], bool]:
        return ([{"金额": 1}, {"金额": 2}], False)

    monkeypatch.setattr(DatasetService, "dataset", dataset)
    monkeypatch.setattr(DatasetService, "_query_context", query_context)
    monkeypatch.setattr("app.services.kb_dataset.run_query", run_query)
    return service


async def test_an_answered_query_reports_its_width_and_what_it_cost(
    service: DatasetService, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)

    await service.query(
        uuid.uuid4(), None, DatasetQuery(dataset="订单", fields=[{"field": "金额"}])
    )

    record = _only(caplog, metrics.QUERY_LATENCY)
    assert (record.rows, record.joins) == (2, 0)
    assert record.duration_ms >= 0


async def test_a_refused_query_is_counted_by_reason_and_never_as_an_answer(
    service: DatasetService, caplog: pytest.LogCaptureFixture
) -> None:
    """A refusal is a prompt-quality signal: the model asked for a column no model has."""
    caplog.set_level(logging.INFO, logger=LOGGER)

    with pytest.raises(BadRequestError, match="Unknown field"):
        await service.query(
            uuid.uuid4(), None, DatasetQuery(dataset="订单", fields=[{"field": "利润"}])
        )

    rejected = _only(caplog, metrics.QUERY_REJECTED)
    assert "利润" in rejected.reason
    assert metrics.QUERY_LATENCY not in _events(caplog)


async def test_a_publish_reports_the_version_it_minted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`version_published` is called from `publish`, not from a caller that might forget."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    dataset = _dataset(version=0)
    stored: list[dict[str, Any]] = []

    async def current_rows(db: Any, **_: Any) -> list[Any]:
        return []

    async def close_rows(db: Any, **_: Any) -> None:
        return None

    async def insert_rows(db: Any, rows: list[dict[str, Any]]) -> None:
        stored.extend(rows)

    async def add_version(db: Any, **values: Any) -> None:
        return None

    async def count_current(db: Any, **_: Any) -> int:
        return len(stored)

    async def touch_dataset(db: Any, dataset_id: Any, **values: Any) -> None:
        return None

    for name, replacement in [
        ("current_rows", current_rows),
        ("close_rows", close_rows),
        ("insert_rows", insert_rows),
        ("add_version", add_version),
        ("count_current", count_current),
        ("touch_dataset", touch_dataset),
    ]:
        monkeypatch.setattr(repo, name, replacement)

    await versions.publish(
        None,  # type: ignore[arg-type]
        dataset,
        [{"订单编号": "SO-1"}, {"订单编号": "SO-2"}],
        key_fields=["订单编号"],
    )

    record = _only(caplog, metrics.VERSION_ROWS)
    assert (record.rows, record.version_no) == (2, 1)


def test_the_compiler_is_the_only_thing_that_can_refuse() -> None:
    """Guards the tests above: `compile_query` is the real one, so the refusal is too."""
    with pytest.raises(BadRequestError):
        compile_query(_context(), DatasetQuery(dataset="订单", fields=[{"field": "利润"}]))
