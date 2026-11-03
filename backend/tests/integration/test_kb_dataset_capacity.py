"""What a dataset costs, measured rather than budgeted (T31).

`docs/design/knowledge-datasets.md` used to state its capacity as a table of
budgets with the mechanism each one rests on, and said so in as many words: "this
build has not been benchmarked on a machine with a real workbook yet". This file
is the measurement that either confirms those numbers or names the one that does
not hold. It runs a real workbook through the real import path and pages a real
dataset, both against real Postgres.

Three things are deliberately not mocked:

- **The workbook.** Cells are written by openpyxl, read back by openpyxl, and
  the shape is the one the design talks about: eight columns, a text key, two
  integers, a date, three low-cardinality texts and a free-text note.
- **The path.** `excel.scan` is the upload screen, `imports.commit` is the
  worker, `DatasetService.list_rows` is the grid in the console. A benchmark of
  `repo.insert_rows` alone would measure libpq and say nothing about an import.
- **The version history.** The second upload changes one row in twenty, so the
  re-import measurement is the one the design calls "comparable to the first":
  the digest compare has to skip 95% of the file and close only what moved.

**Sizes.** The default is small enough to run on every `make test` - it is a
regression guard, and a guard nobody runs is a claim nobody checks. The sizes the
design actually talks about are behind an environment variable:

    KB_CAPACITY_ROWS=1000000 KB_CAPACITY_SAMPLES=200 \\
        python -m pytest tests/integration/test_kb_dataset_capacity.py -s -k costs

The measured run and its numbers are committed in
`loadtest/results/*-dataset-capacity.md`.
"""

import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from io import BytesIO
from typing import Any

import pytest
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import AuthContext
from app.db.models.knowledge_base import KBScope, KnowledgeBase
from app.db.models.organization import Organization
from app.db.models.user import User
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import (
    DatasetFieldInput,
    DatasetImportCommit,
    DatasetImportSheet,
    DatasetMetricInput,
    DatasetQuery,
)
from app.services.kb_dataset import DatasetService, excel, imports
from app.services.kb_dataset.model import create_dataset

pytestmark = pytest.mark.anyio

# The columns of the workbook, and the dataset they become. Eight is the shape of
# the exports this feature was built for: an order id, a foreign key, a date, two
# numbers, two low-cardinality labels and a note - enough that coercion, JSONB and
# the digest compare all have real work to do.
HEADER = ("订单编号", "客户编号", "下单日期", "金额", "数量", "状态", "城市", "备注")

FIELDS = [
    DatasetFieldInput(name="订单编号", data_type="text", is_primary_key=True),
    DatasetFieldInput(name="客户编号", data_type="text"),
    DatasetFieldInput(name="下单日期", data_type="date"),
    DatasetFieldInput(name="金额", data_type="integer"),
    DatasetFieldInput(name="数量", data_type="integer"),
    DatasetFieldInput(name="状态", data_type="text"),
    DatasetFieldInput(name="城市", data_type="text"),
    DatasetFieldInput(name="备注", data_type="text"),
]

METRICS = [
    DatasetMetricInput(name="金额合计", display_name="金额合计", agg="sum", field_name="金额"),
    DatasetMetricInput(name="订单数", display_name="订单数", agg="count", field_name="订单编号"),
]

CUSTOMERS = 20_000
"""How many distinct customers the orders are spread over - a real foreign key,
not a unique column, so `_suggest_primary_key` has a plausible decoy to reject."""

CHANGE_EVERY = 20
"""One row in twenty differs on the second upload. Five percent is the re-import
the design's budget row is about: enough that the digest has to walk the file,
little enough that most of it is skipped."""

SHEETS = ("订单-1", "订单-2", "订单-3")
"""Three sheets for a million rows, because `MAX_ROWS_PER_SHEET` is 500,000 - and
counts Excel's row index, so a full sheet carries 499,999 data rows and its
header. The split is also the case that catches a per-sheet re-parse: before the
import parsed its workbook once, three sheets read and parsed the same file three
times."""

SIZE = int(os.getenv("KB_CAPACITY_ROWS") or 20_000)
SAMPLES = int(os.getenv("KB_CAPACITY_SAMPLES") or 40)

# What a step may cost, as a rate rather than an absolute: the design's budgets
# are per-row claims, and a threshold that has to be edited whenever somebody
# changes the row count is a threshold nobody keeps. Generous on purpose - these
# catch an order-of-magnitude regression, and the numbers the design quotes come
# from the committed run, not from this.
PARSE_SECONDS_PER_ROW = 0.0015
"""The upload screen: parse the workbook and propose the model.

1.5 ms a row is a quarter of the "seconds, not minutes" budget for a 200k sheet,
which is the point: the guard fires when the parse has become the slowest thing
in the product, not when it drifts by ten percent."""

IMPORT_SECONDS_PER_ROW = 0.0025
"""The worker: parse again, coerce, quality-check and publish, per row."""

PAGE_P95_SECONDS = 0.6
"""A page of fifty rows, at the current version or a historical one. The design
claims "a few ms"; this is the ceiling that says the index is doing its job."""


@dataclass
class Report:
    """The table a run prints, and the verdicts it is judged by."""

    rows: list[tuple[str, str, str, bool]] = field(default_factory=list)

    def step(self, name: str, measured: str, budget: str, held: bool) -> None:
        self.rows.append((name, measured, budget, held))

    def markdown(self) -> str:
        lines = [
            "| Step | Measured | Budget | |",
            "| --- | --- | --- | --- |",
            *(
                f"| {name} | {measured} | {budget} | {'ok' if held else 'over'} |"
                for name, measured, budget, held in self.rows
            ),
        ]
        return "\n".join(lines)


def _rows(count: int, *, offset: int = 0, changed: bool = False) -> Iterator[list[Any]]:
    """`count` rows of the shape above, as a generator.

    A generator rather than a list because a million-row workbook is built and
    written cell by cell; holding a million Python lists to hand them to openpyxl
    would measure the test's memory rather than the product's.
    """
    for index in range(count):
        number = offset + index
        amount = 1_000 + number % 5_000
        if changed and index % CHANGE_EVERY == 0:
            amount += 7
        yield [
            f"SO-{number:08d}",
            f"C-{number % CUSTOMERS:06d}",
            "2026-01-15",
            amount,
            1 + number % 9,
            "已发货" if number % 2 else "待付款",
            ("上海", "北京", "广州", "深圳")[number % 4],
            f"load {number}",
        ]


def _workbook(sheets: int, count: int, *, changed: bool = False) -> bytes:
    """One workbook, `count` rows split as evenly as they divide across `sheets`."""
    book = Workbook(write_only=True)
    each, remainder = divmod(count, sheets)
    offset = 0
    for position in range(sheets):
        size = each + (1 if position < remainder else 0)
        sheet = book.create_sheet(SHEETS[position])
        sheet.append(list(HEADER))
        for row in _rows(size, offset=offset, changed=changed):
            sheet.append(row)
        offset += size
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def _ctx(kb: KnowledgeBase) -> AuthContext:
    return AuthContext(user_id=kb.owner_user_id, organization_id=kb.organization_id, role="owner")


async def _base(db: AsyncSession) -> KnowledgeBase:
    owner = User(email=f"{uuid.uuid4()}@example.com", hashed_password="x", is_active=True)
    db.add(owner)
    await db.flush()
    organization = Organization(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}", created_by_user_id=owner.id
    )
    db.add(organization)
    await db.flush()
    kb = KnowledgeBase(
        id=uuid.uuid4(),
        name=f"capacity-{uuid.uuid4().hex[:8]}",
        scope=KBScope.ORG.value,
        collection_name=f"capacity-{uuid.uuid4().hex[:8]}",
        kb_type="dataset",
        embedding_model="text-embedding-3-small",
        embedding_dim=1536,
        embedding_provider="openrouter",
        organization_id=organization.id,
        owner_user_id=owner.id,
        visibility="org",
    )
    db.add(kb)
    await db.flush()
    return kb


async def _staged(db: AsyncSession, kb: KnowledgeBase, path: Any, content: bytes) -> Any:
    """An import record as the upload route leaves it: staged, parsed, at S1.

    `imports.preview` is not called - it writes into the deployment's media
    directory, which this suite does not own. What it does with the bytes is
    `excel.scan` and a plan, and both are here.
    """
    path.write_bytes(content)
    proposals = excel.scan(content)
    plan = [imports.proposal_payload(proposal) for proposal in proposals]
    record = await repo.create_import(
        db,
        knowledge_base_id=kb.id,
        organization_id=kb.organization_id,
        status="preview",
        filename="订单.xlsx",
        file_size=len(content),
        file_digest="0" * 64,
        sheet_count=len(plan),
        plan={"sheets": plan},
        summary={},
        created_by=kb.owner_user_id,
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


async def _import(
    db: AsyncSession,
    kb: KnowledgeBase,
    path: Any,
    dataset: Any,
    content: bytes,
    *,
    breakdown: dict[str, float] | None = None,
) -> float:
    """Run one whole import through the worker's own entry point, in seconds.

    `breakdown`, when given, is filled with the seconds spent in the four things
    a publish does inside its transaction. A single total is enough to say an
    import is slow; it is not enough to say what to change, and the re-import at a
    million rows is slow enough to be worth knowing.
    """
    record = await _staged(db, kb, path, content)
    payload = _commit(*((name, dataset) for name in SHEETS))
    with _timed_steps(breakdown if breakdown is not None else {}):
        started = time.perf_counter()
        outcome = await imports.commit(
            db,
            record=record,
            knowledge_base_id=kb.id,
            organization_id=kb.organization_id,
            payload=payload,
            actor_user_id=kb.owner_user_id,
        )
        seconds = time.perf_counter() - started
    assert outcome["status"] == "committed", outcome
    # The identity map holds every row publish() loaded for its interval
    # compare; keeping a million of them across a second import would measure
    # this process's memory pressure instead of the database's work.
    db.expunge_all()
    return seconds


@contextmanager
def _timed_steps(breakdown: dict[str, float]) -> Iterator[None]:
    """Time `publish`'s four expensive steps.

    Patched at the repository rather than at the service: this is where the cost
    is, and the four names are the whole of what an import does inside its
    transaction - load the current version, close what moved, insert what is new,
    count the result. A single total says an import is slow; it does not say what
    to change.
    """
    originals = {attribute: getattr(repo, attribute) for _, attribute in _STEPS}

    def counted(name: str, function: Any) -> Any:
        @wraps(function)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return await function(*args, **kwargs)
            finally:
                breakdown[name] = breakdown.get(name, 0.0) + (time.perf_counter() - started)

        return wrapper

    try:
        for name, attribute in _STEPS:
            setattr(repo, attribute, counted(name, originals[attribute]))
        yield
    finally:
        for attribute, function in originals.items():
            setattr(repo, attribute, function)


_STEPS = (
    ("load current version", "current_rows"),
    ("close changed intervals", "close_rows"),
    ("insert new intervals", "insert_rows"),
    ("count current rows", "count_current"),
)


async def _page(
    service: DatasetService, ctx: AuthContext, dataset_id: uuid.UUID, *, version_no: int | None
) -> list[float]:
    """`SAMPLES` pages of fifty rows, walking the first few hundred rows.

    Walking pages rather than jumping to a random offset is what the console
    does, and an `OFFSET` of 500,000 is a different measurement - one the design
    does not claim. The deep page is measured separately and reported as a note.
    """
    timings: list[float] = []
    for index in range(SAMPLES):
        skip = (index % 10) * 50
        started = time.perf_counter()
        page = await service.list_rows(
            dataset_id, ctx, version_no=version_no, search=None, skip=skip, limit=50
        )
        timings.append((time.perf_counter() - started) * 1000)
        assert len(page.items) == 50, f"page {skip} returned {len(page.items)} rows"
    return timings


@pytest.mark.skipif(
    SIZE > 100_000,
    reason="a million-row measurement is a run, not a test - see loadtest/results",
)
async def test_the_indexes_paging_depends_on_exist(db: AsyncSession) -> None:
    """The two indexes the design's paging budgets rest on, as the catalogue has them.

    A migration that drops one of these changes a millisecond into a second and
    nothing else notices: the query still answers, the tests still pass, and the
    console gets slower every month. Asked of `pg_indexes` rather than of the
    model, because the model is what would be edited in the same commit.
    """
    names = {
        row[0]
        for row in await db.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'kb_data'")
        )
    }
    assert {"dataset_rows_open_uq", "dataset_rows_history_idx"} <= names


async def test_what_a_workbook_costs(db: AsyncSession, tmp_path: Any) -> None:
    """Import it twice, page it at two versions, and print what each step cost."""
    report = Report()
    kb = await _base(db)
    dataset = await create_dataset(
        db,
        knowledge_base_id=kb.id,
        organization_id=kb.organization_id,
        name="订单明细",
        fields=FIELDS,
        metrics=METRICS,
        created_by=kb.owner_user_id,
    )
    ctx = _ctx(kb)

    first = _workbook(len(SHEETS), SIZE)
    staged = tmp_path / "orders.xlsx"

    started = time.perf_counter()
    proposals = excel.scan(first)
    parse_seconds = time.perf_counter() - started
    assert len(proposals) == len(SHEETS)
    assert sum(len(proposal.sheet.rows) for proposal in proposals) == SIZE
    del proposals

    report.step(
        f"Parse + propose {SIZE:,} rows",
        f"{parse_seconds:.1f} s ({SIZE / parse_seconds:,.0f} rows/s)",
        f"≤ {SIZE * PARSE_SECONDS_PER_ROW:.0f} s",
        parse_seconds <= SIZE * PARSE_SECONDS_PER_ROW,
    )

    publish_steps: dict[str, float] = {}
    publish_seconds = await _import(db, kb, staged, dataset, first, breakdown=publish_steps)
    report.step(
        f"Publish first version, {SIZE:,} rows",
        f"{publish_seconds:.1f} s ({SIZE / publish_seconds:,.0f} rows/s)",
        f"≤ {SIZE * IMPORT_SECONDS_PER_ROW:.0f} s",
        publish_seconds <= SIZE * IMPORT_SECONDS_PER_ROW,
    )

    dataset = await repo.get_dataset(db, dataset.id)
    assert dataset is not None
    assert dataset.row_count == SIZE
    first_version = dataset.current_version_no

    second = _workbook(len(SHEETS), SIZE, changed=True)
    reimport_steps: dict[str, float] = {}
    reimport_seconds = await _import(db, kb, staged, dataset, second, breakdown=reimport_steps)
    report.step(
        f"Re-import, 1 row in {CHANGE_EVERY} changed",
        f"{reimport_seconds:.1f} s ({SIZE / reimport_seconds:,.0f} rows/s)",
        f"≤ {SIZE * IMPORT_SECONDS_PER_ROW:.0f} s",
        reimport_seconds <= SIZE * IMPORT_SECONDS_PER_ROW,
    )

    dataset = await repo.get_dataset(db, dataset.id)
    assert dataset is not None
    assert dataset.current_version_no > first_version
    assert dataset.row_count == SIZE

    service = DatasetService(db)
    current = await _page(service, ctx, dataset.id, version_no=None)
    report.step(
        "Page 50 rows, current version (p95)",
        f"{_percentile(current, 0.95):.1f} ms",
        f"≤ {PAGE_P95_SECONDS * 1000:.0f} ms",
        _percentile(current, 0.95) <= PAGE_P95_SECONDS * 1000,
    )
    history = await _page(service, ctx, dataset.id, version_no=first_version)
    report.step(
        f"Page 50 rows, as of v{first_version} (p95)",
        f"{_percentile(history, 0.95):.1f} ms",
        f"≤ {PAGE_P95_SECONDS * 1000:.0f} ms",
        _percentile(history, 0.95) <= PAGE_P95_SECONDS * 1000,
    )

    # One aggregate over the whole dataset, through the compiler the agent uses -
    # the design's "well under the 8 s ceiling" row.
    started = time.perf_counter()
    answer = await service.query(
        dataset.id,
        ctx,
        DatasetQuery(
            dataset=dataset.name,
            fields=[{"metric": "金额合计", "alias": "total"}, {"metric": "订单数", "alias": "n"}],
        ),
    )
    aggregate_seconds = time.perf_counter() - started
    assert answer.rows and answer.rows[0]["n"] == SIZE, answer.rows
    report.step(
        f"Aggregate over {SIZE:,} rows",
        f"{aggregate_seconds:.2f} s",
        "≤ 8 s (statement_timeout)",
        aggregate_seconds <= 8,
    )

    print()
    print(f"Dataset capacity, {SIZE:,} rows, {SAMPLES} page samples")
    print(report.markdown())
    print()
    print("Where the two publishes spent their time (inside the transaction):")
    print()
    print("| Step | First publish | Re-import |")
    print("| --- | --- | --- |")
    for name, _ in _STEPS:
        print(
            f"| {name} | {publish_steps.get(name, 0.0):.1f} s | {reimport_steps.get(name, 0.0):.1f} s |"
        )
    outside_first = publish_seconds - sum(publish_steps.values())
    outside_reimport = reimport_seconds - sum(reimport_steps.values())
    print(
        f"| parse, coerce, compare, digest (not timed above) "
        f"| {outside_first:.1f} s | {outside_reimport:.1f} s |"
    )

    # A jump to the middle of a million rows is a different query from the one a
    # person makes, and the design does not claim it - reported so that the
    # number exists rather than being discovered later.
    started = time.perf_counter()
    deep = await service.list_rows(
        dataset.id, ctx, version_no=None, search=None, skip=SIZE // 2, limit=50
    )
    print(
        f"| Page 50 rows at offset {SIZE // 2:,} (not a claimed budget) "
        f"| {(time.perf_counter() - started) * 1000:.0f} ms, {len(deep.items)} rows | - | - |"
    )

    over = [name for name, _, _, held in report.rows if not held]
    assert not over, "these steps exceeded their budget:\n" + report.markdown()
