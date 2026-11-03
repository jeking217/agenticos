"""Uploading a workbook: preview first, publish on purpose.

The two halves are separate requests on purpose. Preview parses the file and
writes nothing to any dataset; commit re-reads the same staged file and folds
the chosen sheets into versions. Between them a person can fix a header row, drop
a summary sheet, or decide that sheet 3 is a different dataset entirely - and
none of those decisions can be made by a parser's guess.

A workbook with five sheets is five publishes. One sheet failing does not undo
the four that worked: the import ends `partial`, names what failed, and can be
retried sheet by sheet. A transaction that rolled all five back would make the
person re-upload a 40 MB file to fix one column header.
"""

import hashlib
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.blocking import run_blocking
from app.core.config import settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.db.models.kb_dataset import Dataset
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import (
    DatasetFieldInput,
    DatasetImportCommit,
    DatasetImportDraft,
    DatasetImportRelation,
    DatasetImportSheet,
    DatasetRelationInput,
    SheetProposal,
)
from app.services.background_jobs import enqueue
from app.services.kb_dataset import excel, metrics, quality, quota, reasoning
from app.services.kb_dataset import relations as relations_service
from app.services.kb_dataset.model import create_dataset, field_types, primary_key
from app.services.kb_dataset.versions import publish
from app.worker.jobs import IMPORT_DATASET, ImportDataset, ImportSheet

_ACCEPTED = (".xlsx", ".xlsm", ".xls")
_STAGING = "_kb_dataset_imports"

QUALITY_PREVIEW_ROWS = 5000
"""How much of a sheet the preview reports on. The commit reports all of it."""

QUEUED = "queued"
"""Accepted by the API and waiting for a worker - not yet published by anything."""
RUNNING = "running"
"""A worker holds it. The poller reads its `summary` for how far it got."""
NOT_STARTED = frozenset({"draft", "preview"})
"""Parsed, and either not yet read or not yet accepted. Nothing to publish."""


class Progress:
    """Where a running import says how far it got.

    In a request this only touches `summary`: the whole commit is one
    transaction, so a mid-way write would either be invisible to a poller on
    another connection or commit work this request could still roll back.
    """

    async def report(self, record: Any, summary: dict[str, Any]) -> None:
        record.summary = summary


class SavedProgress(Progress):
    """For an import running in a worker: each sheet is its own transaction.

    Committing per sheet is what makes a poll see progress at all, and what lets
    a workbook of five sheets survive a worker dying on the third: the two that
    landed stay landed, the retry runs the rest, and a sheet it ran again is a
    no-op because identical rows mint no version.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def report(self, record: Any, summary: dict[str, Any]) -> None:
        record.summary = summary
        await self.db.commit()


def progress_of(record: Any) -> dict[str, Any]:
    """The block a poller reads: how many sheets are done, and where it is now.

    A record written before the queue has no `total` and no `done`, so both are
    derived from the sheets it recorded: an import that finished last month is
    not shown as one that never started.

    An import that nothing has accepted yet is the one case where there is no
    progress to report at all, and it is reported as such. It used to fall
    through to the "finished" branch - zero sheets out of zero read as 100% - so
    the wizard's first screen opened on a full bar and a phase of `done` for a
    workbook that had not been published (#T29).
    """
    if record.status in NOT_STARTED:
        return {
            "phase": record.status,
            "total": 0,
            "done": 0,
            "failed": 0,
            "current_sheet": None,
            "percent": 0,
        }
    summary = record.summary or {}
    settled = list(summary.get("sheets") or [])
    total = int(summary.get("total") or len(settled))
    done = int(summary.get("done") or len(settled))
    failed = int(summary.get("failed") or 0)
    finished = record.status not in {QUEUED, RUNNING}
    settled_percent = min(round(done * 100 / total), 100) if total else 100
    # While it runs the bar cannot claim the last point: the sheets are settled
    # but the last write - the version, the audit, the released file - is not.
    percent = settled_percent if finished else min(settled_percent, 99)
    return {
        "phase": "done" if finished else ("running" if record.status == RUNNING else QUEUED),
        "total": total,
        "done": done,
        "failed": failed,
        "current_sheet": summary.get("current_sheet"),
        "percent": percent,
    }


def staging_dir() -> Path:
    directory = Path(settings.MEDIA_DIR) / _STAGING
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _check_filename(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in _ACCEPTED:
        raise BadRequestError(message="Only Excel workbooks can be imported (.xlsx, .xlsm or .xls)")
    return suffix


def proposal_payload(proposal: excel.SheetProposal) -> dict[str, Any]:
    sheet = proposal.sheet
    return {
        "sheet_name": sheet.name,
        "kind": sheet.kind,
        "header_row": sheet.header_row,
        "row_count": len(sheet.rows),
        "column_count": len(sheet.headers),
        "merged_cells": sheet.merged_cells,
        "warnings": list(sheet.warnings),
        "fields": [
            {
                "name": field.name,
                "display_name": field.display_name,
                "data_type": field.data_type,
                "is_nullable": field.is_nullable,
                "is_primary_key": field.is_primary_key,
                "distinct_ratio": field.distinct_ratio,
                "samples": field.samples,
            }
            for field in proposal.fields
        ],
        "preview": proposal.preview,
        "quality": quality_of(proposal),
    }


def quality_of(proposal: excel.SheetProposal) -> dict[str, Any] | None:
    """The report the wizard shows at S5, from a sample of the sheet.

    Sampled on purpose: the commit below reports the whole sheet and its numbers
    are the ones recorded. A preview that walked 500k rows to answer a question
    the next step answers properly would be the slowest screen in the flow.
    """
    sheet = proposal.sheet
    if sheet.kind != "data" or not proposal.fields:
        return None
    types = {field.name: field.data_type for field in proposal.fields}
    rows = excel.coerce_rows(sheet, types, limit=QUALITY_PREVIEW_ROWS)
    return quality.report(
        sheet.name,
        rows,
        types=types,
        key_fields=proposal.primary_key,
        first_row=(sheet.header_row or 1) + 1,
        total=len(sheet.rows),
    ).to_dict()


async def preview(
    db: AsyncSession,
    *,
    knowledge_base_id: uuid.UUID,
    organization_id: uuid.UUID,
    filename: str,
    content: bytes,
    actor_user_id: uuid.UUID | None,
) -> Any:
    _check_filename(filename)
    limit = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > limit:
        raise BadRequestError(
            message=f"The workbook is larger than the {settings.MAX_UPLOAD_SIZE_MB} MB limit"
        )
    if not content:
        raise BadRequestError(message="The uploaded file is empty")

    record = await repo.create_import(
        db,
        knowledge_base_id=knowledge_base_id,
        organization_id=organization_id,
        status="draft",
        filename=filename[:255],
        file_size=len(content),
        file_digest=hashlib.sha256(content).hexdigest(),
        sheet_count=0,
        plan={},
        summary={},
        created_by=actor_user_id,
    )

    path = staging_dir() / f"{record.id}{Path(filename).suffix.lower()}"
    await run_blocking(path.write_bytes, content)
    record.staged_path = str(path)

    try:
        proposals = await run_blocking(excel.scan, content)
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)[:1000]
        raise BadRequestError(message=f"Could not read the workbook: {exc}") from exc

    sheets = [proposal_payload(proposal) for proposal in proposals]
    record.status = "preview"
    record.sheet_count = len(sheets)
    record.plan = {"sheets": sheets, "filename": filename, "step": "model"}
    return record


def plan_of(record: Any) -> dict[str, Any]:
    """The parsed workbook and the decisions saved against it.

    What the wizard reads to resume: the sheet proposals it was shown, which
    step the person reached, and the choices they had made at it.
    """
    plan = record.plan or {}
    return {
        "filename": plan.get("filename") or record.filename,
        "step": plan.get("step") or "model",
        "sheets": plan.get("sheets") or [],
        "choices": plan.get("choices") or None,
    }


async def analyze(
    db: AsyncSession,
    *,
    record: Any,
    kb: Any,
    force: bool = False,
) -> dict[str, Any]:
    """Ask the base's reasoning model what this workbook means, before publishing.

    Its own request rather than part of the upload, because the two answer
    different questions and take different amounts of time: the parse is a
    second on a big file, and a model reading six sheets is several. A wizard
    that showed nothing until the slower of the two finished would make every
    import feel like a model call, and one where the model is down would fail
    the upload itself.

    Nothing here can refuse an import. No model configured, a key that was
    deleted, a provider that timed out: the answer says `ran: false` and why,
    and the wizard carries on with the parser's proposal.

    The result is kept on the import, so closing the dialog and reopening it
    does not buy a second model call, and `force` is how somebody asks for one.
    """
    if record.status not in {"draft", "preview"}:
        raise BadRequestError(message="This import has already been published from")
    cached = (record.plan or {}).get("advice")
    if cached and not force:
        return {**cached, "cached": True}
    if not record.staged_path or not Path(record.staged_path).exists():
        raise NotFoundError(message="The uploaded workbook is no longer available; upload it again")

    suggestions: list[dict[str, Any]] = []
    for dataset in await repo.get_dataset_by_kb(db, kb.id):
        if dataset.archived_at is not None:
            continue
        fields = await repo.list_fields(db, dataset.id)
        suggestions.append(
            {
                "name": dataset.name,
                "columns": [{"name": field.name, "type": field.data_type} for field in fields],
            }
        )

    content = await run_blocking(Path(record.staged_path).read_bytes)
    proposals = await run_blocking(excel.scan, content)
    outcome = await reasoning.advise(db, kb, proposals, existing=suggestions)

    if not outcome.ran:
        result: dict[str, Any] = {
            "ran": False,
            "reason": outcome.reason,
            "provider": outcome.provider,
            "model": outcome.model,
            "sheets": (record.plan or {}).get("sheets") or [proposal_payload(p) for p in proposals],
            "relations": [],
        }
        record.plan = {**(record.plan or {}), "advice": result}
        await db.flush()
        return result

    assert outcome.advice is not None  # narrowed by `ran`
    applied = reasoning.apply(proposals, outcome.advice)
    sheets = []
    for proposal in applied.proposals:
        payload = proposal_payload(proposal)
        # Which columns the model spoke about, so the wizard can say so on the
        # row rather than silently show a type nobody can attribute.
        decided = applied.fields.get(payload["sheet_name"], set())
        for field in payload["fields"]:
            field["advised"] = field["name"] in decided
        payload["advised"] = payload["sheet_name"] in applied.sheets or bool(decided)
        sheets.append(payload)

    result = {
        "ran": True,
        "reason": None,
        "provider": outcome.provider,
        "model": outcome.model,
        "sheets": sheets,
        "relations": applied.relations,
    }
    record.plan = {**(record.plan or {}), "advice": result}
    await db.flush()
    return result


async def save_draft(db: AsyncSession, *, record: Any, draft: DatasetImportDraft) -> dict[str, Any]:
    """Keep the wizard's decisions, step and all, without publishing anything.

    Every sheet named must be one this import parsed: a saved decision about a
    sheet that is not in the workbook is a stale browser tab, and accepting it
    would let the commit publish a choice nobody can see.
    """
    if record.status not in {"draft", "preview"}:
        raise BadRequestError(message="This import has already been published from")
    for sheet in draft.sheets:
        _proposal_of(record, sheet.sheet_name)
    record.plan = {
        **(record.plan or {}),
        "step": draft.step,
        "choices": draft.model_dump(mode="json"),
    }
    if record.status == "draft":
        record.status = "preview"
    await db.flush()
    return plan_of(record)


def _refuse_unknown_columns(sheet_name: str, unknown: list[str]) -> None:
    """Stop a sheet whose headers do not match the dataset's model.

    A helper rather than an inline `raise` because the call sits inside a
    savepoint inside a loop: an `if` that only raises reads as control flow that
    belongs here, and ruff's TRY301 is right that it does not.
    """
    if not unknown:
        return
    raise BadRequestError(
        message=(
            f"Sheet `{sheet_name}` has column(s) the dataset does not model: {', '.join(unknown)}"
        )
    )


def _settle_invalid(
    entry: Any, rows: list[dict[str, Any]], report: quality.Report, *, first_row: int
) -> tuple[list[dict[str, Any]], int]:
    """Apply the sheet's `on_invalid` policy.

    `block` refuses the whole sheet, and the refusal names the rows: a person
    fixes the workbook rather than wondering which three rows were dropped. It
    is the default because the alternative is a publish that quietly leaves data
    out - the one outcome nobody can see in the result.
    """
    if not report.has_blocking:
        return rows, 0
    if getattr(entry, "on_invalid", quality.BLOCK_POLICY) != quality.SKIP_POLICY:
        quality.refuse(report)
    kept = quality.without_invalid(rows, first_row=first_row, report=report)
    if not kept:
        quality.refuse_all_withheld(report)
    return kept, len(rows) - len(kept)


def _proposal_of(record: Any, sheet_name: str) -> dict[str, Any]:
    for sheet in (record.plan or {}).get("sheets", []):
        if sheet.get("sheet_name") == sheet_name:
            return sheet
    raise BadRequestError(message=f"Sheet `{sheet_name}` is not in this import")


def _parsed_sheet(sheets: dict[str, excel.ParsedSheet], sheet_name: str) -> excel.ParsedSheet:
    """One sheet out of the workbook parsed for this import, taken so it can go.

    Removed from the dict rather than returned by reference: a million-row sheet
    is a megabyte-heavy object, and holding every sheet of a workbook in parsed
    form for the whole commit is memory nobody needs once its version is
    published.
    """
    parsed = sheets.pop(sheet_name, None)
    if parsed is None:
        raise KeyError(f"Sheet {sheet_name!r} is not in the workbook")
    return parsed


def _fields_from_proposal(sheet: dict[str, Any]) -> list[DatasetFieldInput]:
    return [
        DatasetFieldInput(
            name=field["name"],
            display_name=field.get("display_name"),
            data_type=field.get("data_type", "text"),
            is_nullable=field.get("is_nullable", True),
            is_primary_key=field.get("is_primary_key", False),
        )
        for field in sheet.get("fields", [])
    ]


async def _resolve_dataset(
    db: AsyncSession,
    *,
    record: Any,
    knowledge_base_id: uuid.UUID,
    organization_id: uuid.UUID,
    entry: Any,
    sheet: dict[str, Any],
    actor_user_id: uuid.UUID | None,
) -> Dataset:
    if entry.dataset_id is not None:
        dataset = await repo.get_dataset(db, entry.dataset_id)
        if dataset is None or dataset.knowledge_base_id != knowledge_base_id:
            raise BadRequestError(message="The target dataset is not in this knowledge base")
        if dataset.archived_at is not None:
            raise BadRequestError(message="The target dataset is archived")
        return dataset

    name = (entry.dataset_name or entry.sheet_name)[:128]
    fields = list(entry.fields) if entry.fields else _fields_from_proposal(sheet)
    if entry.primary_key_fields:
        names = set(entry.primary_key_fields)
        for field in fields:
            field.is_primary_key = field.name in names
    if not any(field.is_primary_key for field in fields):
        raise BadRequestError(
            message=f"Sheet `{entry.sheet_name}` needs a primary key before it can be imported"
        )
    return await create_dataset(
        db,
        knowledge_base_id=knowledge_base_id,
        organization_id=organization_id,
        name=name,
        fields=fields,
        sheet_name=entry.sheet_name,
        source_filename=record.filename,
        note=f"Created from {record.filename}",
        created_by=actor_user_id,
    )


def _summary(
    *, total: int, sheets: list[dict[str, Any]], failures: int, current: str | None, phase: str
) -> dict[str, Any]:
    """The import's whole record of itself, rebuilt rather than mutated.

    A new dict each time, because the record is written through `progress` and a
    mutated one would be the same object the session already holds - the write
    that makes progress visible would be the one SQLAlchemy has nothing to see.
    """
    return {
        "phase": phase,
        "total": total,
        "done": len(sheets),
        "failed": failures,
        "current_sheet": current,
        "sheets": list(sheets),
        "failures": failures,
    }


async def _create_relations(
    db: AsyncSession,
    *,
    knowledge_base_id: uuid.UUID,
    requested: list[Any],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create the joins the wizard accepted, resolving names to the datasets they became.

    A suggestion is named after a sheet, because at the moment it is offered the
    dataset that sheet becomes does not exist. Publishing is what creates it, so
    this runs afterwards and translates: a sheet that failed to publish drops the
    suggestion, and a name that matches an existing dataset is taken as one.

    Confirmed, not candidate. The person saw the suggestion, kept it and pressed
    publish - asking them to confirm the same join a second time on another screen
    is the double work the recommendation existed to remove. It is still editable
    from the relation panel, which is what "candidate" was for.
    """
    if not requested:
        return []
    by_name: dict[str, uuid.UUID] = {}
    for outcome in results:
        if outcome.get("status") != "committed":
            continue
        identifier = outcome.get("dataset_id")
        if identifier is None:
            continue
        for key in (outcome.get("sheet_name"), outcome.get("dataset_name")):
            if key:
                by_name[key] = uuid.UUID(str(identifier))
    for dataset in await repo.get_dataset_by_kb(db, knowledge_base_id):
        if dataset.archived_at is None:
            by_name.setdefault(dataset.name, dataset.id)

    created: list[dict[str, Any]] = []
    for entry in requested:
        record: dict[str, Any] = {
            "from_sheet": entry.from_sheet,
            "to_sheet": entry.to_sheet,
            "from_fields": list(entry.from_fields),
            "to_fields": list(entry.to_fields),
        }
        dataset_id = by_name.get(entry.from_sheet)
        target_id = by_name.get(entry.to_sheet)
        if dataset_id is None or target_id is None:
            missing = entry.from_sheet if dataset_id is None else entry.to_sheet
            record.update({"created": False, "error": f"`{missing}` was not published"})
            created.append(record)
            continue
        dataset = await repo.get_dataset(db, dataset_id)
        target = await repo.get_dataset(db, target_id)
        if dataset is None or target is None:
            record.update({"created": False, "error": "the related dataset is gone"})
            created.append(record)
            continue
        name = (entry.name or f"to_{target.name}")[:64]
        try:
            async with db.begin_nested():
                relation, _warnings = await relations_service.create(
                    db,
                    dataset,
                    DatasetRelationInput(
                        target_dataset_id=target.id,
                        name=name,
                        cardinality=entry.cardinality,
                        join_type="left",
                        local_fields=list(entry.from_fields),
                        foreign_fields=list(entry.to_fields),
                        status="confirmed",
                    ),
                )
            record.update({"created": True, "relation_id": str(relation.id), "name": name})
        except Exception as exc:  # a refused join is a line in the report, not a failure
            record.update({"created": False, "error": str(exc)[:300]})
        created.append(record)
    return created


async def commit(
    db: AsyncSession,
    *,
    record: Any,
    knowledge_base_id: uuid.UUID,
    organization_id: uuid.UUID,
    payload: DatasetImportCommit,
    actor_user_id: uuid.UUID | None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    # `queued`/`running` are here because the worker calls this on a record the
    # API already accepted. A person submitting twice is refused where the job
    # is queued, not here: by the time this runs the decision is made.
    if record.status not in {
        "draft",
        "preview",
        "partial",
        "failed",
        QUEUED,
        RUNNING,
    }:
        raise BadRequestError(message="This import has already been committed")
    if not record.staged_path or not Path(record.staged_path).exists():
        raise NotFoundError(message="The uploaded workbook is no longer available; upload it again")

    sink = progress or Progress()
    content = await run_blocking(Path(record.staged_path).read_bytes)
    results: list[dict[str, Any]] = []
    failures = 0
    failed_stages: dict[str, int] = {}
    total = len(payload.sheets)
    started = time.perf_counter()
    # Parsed on the first sheet that needs it, once for the whole workbook.
    # `rows_of` used to re-read the file for each sheet, which made an import of
    # six sheets parse the same bytes six times.
    sheets: dict[str, excel.ParsedSheet] | None = None
    await sink.report(
        record, _summary(total=total, sheets=results, failures=0, current=None, phase="running")
    )

    for entry in payload.sheets:
        # Reported before the work, so the poller can name the sheet that is
        # taking the time rather than only counting the ones already done.
        await sink.report(
            record,
            _summary(
                total=total,
                sheets=results,
                failures=failures,
                current=entry.sheet_name,
                phase="running",
            ),
        )
        # Which step this sheet is on, so a failure can be counted against the one
        # that actually broke rather than against "the import".
        stage = "resolve"
        try:
            # One savepoint per sheet: a sheet that fails half-way leaves no
            # half-created dataset and no interval nobody can explain, while the
            # sheets before and after it still land.
            async with db.begin_nested():
                sheet = _proposal_of(record, entry.sheet_name)
                dataset = await _resolve_dataset(
                    db,
                    record=record,
                    knowledge_base_id=knowledge_base_id,
                    organization_id=organization_id,
                    entry=entry,
                    sheet=sheet,
                    actor_user_id=actor_user_id,
                )
                stage = "parse"
                if sheets is None:
                    sheets = await run_blocking(excel.sheets_of, content)
                parsed = _parsed_sheet(sheets, entry.sheet_name)
                stage = "validate"
                types = await field_types(db, dataset)
                columns = set(parsed.headers)
                _refuse_unknown_columns(entry.sheet_name, sorted(columns - set(types)))
                rows = excel.coerce_rows(parsed, types)
                key_fields = await primary_key(db, dataset)
                first_row = (parsed.header_row or 1) + 1
                report = quality.report(
                    entry.sheet_name,
                    rows,
                    types=types,
                    key_fields=key_fields,
                    first_row=first_row,
                )
                rows, withheld = _settle_invalid(entry, rows, report, first_row=first_row)
                quota.refuse_an_oversized_version(entry.sheet_name, len(rows))
                stage = "write"
                result = await publish(
                    db,
                    dataset,
                    rows,
                    key_fields=key_fields,
                    kind="import",
                    source_filename=record.filename,
                    sheet_name=entry.sheet_name,
                    import_id=record.id,
                    note=payload.note,
                    actor_user_id=actor_user_id,
                )
                outcome = {
                    "sheet_name": entry.sheet_name,
                    "dataset_id": str(dataset.id),
                    "dataset_name": dataset.name,
                    "status": "committed",
                    "version_no": result.version_no,
                    "published": result.published,
                    "rows_added": result.rows_added,
                    "rows_changed": result.rows_changed,
                    "rows_unchanged": result.rows_unchanged,
                    "rows_withheld": withheld,
                    "quality": report.to_dict(),
                    "warnings": result.warnings,
                }
            results.append(outcome)
        except Exception as exc:
            failures += 1
            failed_stages[stage] = failed_stages.get(stage, 0) + 1
            results.append(
                {
                    "sheet_name": entry.sheet_name,
                    "status": "failed",
                    "error": str(exc)[:500],
                    "details": getattr(exc, "details", None),
                }
            )
        # Counted even when it failed: `done` is how much of the workbook has
        # been settled, and a sheet that failed is settled - the bar reaching
        # 100% while saying one failed is the honest reading.
        await sink.report(
            record,
            _summary(total=total, sheets=results, failures=failures, current=None, phase="running"),
        )

    record.status = (
        "partial"
        if failures and failures < len(payload.sheets)
        else ("failed" if failures else "committed")
    )
    metrics.import_finished(
        sheets=len(payload.sheets),
        rows=sum(result.get("rows_added") or 0 for result in results),
        duration_ms=int((time.perf_counter() - started) * 1000),
        failed_stages=failed_stages,
    )
    record.committed_at = datetime.now(UTC)
    # The wizard has nothing left to resume: the decisions are now versions.
    record.plan = {**(record.plan or {}), "step": "publish"}
    if record.status == "failed":
        record.error = "; ".join(
            result.get("error", "") for result in results if result["status"] == "failed"
        )[:1000]

    # Relations last, and only once there are datasets to point at: a workbook's
    # sheets become datasets during this loop, so a join named before it ran
    # would have nowhere to land. Each is its own savepoint for the same reason a
    # sheet is - a relation the compiler would refuse is a row in the report, not
    # a reason to lose the import that already published.
    relations = await _create_relations(
        db,
        knowledge_base_id=knowledge_base_id,
        requested=payload.relations,
        results=results,
    )

    # The last write, so a worker commits the final status with the last sheet
    # rather than leaving the poller on 100% and `running`.
    await sink.report(
        record,
        {
            **_summary(total=total, sheets=results, failures=failures, current=None, phase="done"),
            "relations": relations,
        },
    )

    # The staged workbook has done its job. Removing it is best-effort: a
    # finished import whose file lingers is a disk-space problem, not a data one.
    try:
        Path(record.staged_path).unlink(missing_ok=True)
        record.staged_path = None
    except OSError:
        pass
    return {
        "status": record.status,
        "sheets": results,
        "failures": failures,
        # In the answer as well as on the record: the wizard shows the report
        # from whichever one it has, and a caller that waited (rather than
        # polling) has only this.
        "relations": relations,
    }


def job_payload(
    record: Any,
    *,
    knowledge_base_id: uuid.UUID,
    organization_id: uuid.UUID,
    payload: DatasetImportCommit,
    actor_user_id: uuid.UUID | None,
) -> ImportDataset:
    """The job's copy of a commit request: identifiers, decisions, no session."""
    return ImportDataset(
        import_id=record.id,
        knowledge_base_id=knowledge_base_id,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        sheets=[
            ImportSheet(
                sheet_name=sheet.sheet_name,
                dataset_id=sheet.dataset_id,
                dataset_name=sheet.dataset_name,
                fields=(
                    [field.model_dump(mode="json") for field in sheet.fields]
                    if sheet.fields is not None
                    else None
                ),
                primary_key_fields=sheet.primary_key_fields,
                on_invalid=sheet.on_invalid,
            )
            for sheet in payload.sheets
        ],
        note=payload.note,
        relations=[entry.model_dump(mode="json") for entry in payload.relations],
    )


def commit_spec(job: ImportDataset) -> DatasetImportCommit:
    """The job's payload back as the request the service publishes from."""
    return DatasetImportCommit(
        sheets=[
            DatasetImportSheet(
                sheet_name=sheet.sheet_name,
                dataset_id=sheet.dataset_id,
                dataset_name=sheet.dataset_name,
                fields=sheet.fields,
                primary_key_fields=sheet.primary_key_fields,
                on_invalid=sheet.on_invalid,
            )
            for sheet in job.sheets
        ],
        note=job.note,
        relations=[DatasetImportRelation(**entry) for entry in job.relations],
    )


def _refuse_a_repeat(record: Any) -> None:
    """A commit is accepted once: a second one while the first is out would
    publish the same workbook twice and mint a version for a no-op."""
    if record.status in {QUEUED, RUNNING}:
        raise BadRequestError(message="This import is already being published")
    if record.status == "committed":
        raise BadRequestError(message="This import has already been committed")
    if not record.staged_path or not Path(record.staged_path).exists():
        raise NotFoundError(message="The uploaded workbook is no longer available; upload it again")


async def accept(
    db: AsyncSession,
    *,
    record: Any,
    knowledge_base_id: uuid.UUID,
    organization_id: uuid.UUID,
    payload: DatasetImportCommit,
    actor_user_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Accept a commit and queue it; answer before the workbook is read.

    The job is written in the caller's transaction, so the row that says
    `queued` and the job that will move it commit together. Deduplicated on the
    import id: a double-click queues one publish, not two.
    """
    _refuse_a_repeat(record)
    record.status = QUEUED
    record.summary = _summary(
        total=len(payload.sheets), sheets=[], failures=0, current=None, phase="queued"
    )
    await enqueue(
        db,
        IMPORT_DATASET,
        job_payload(
            record,
            knowledge_base_id=knowledge_base_id,
            organization_id=organization_id,
            payload=payload,
            actor_user_id=actor_user_id,
        ),
        organization_id=organization_id,
        dedupe_key=f"dataset-import:{record.id}",
        dedupe_active=True,
    )
    return {"import_id": record.id, "status": record.status, "progress": progress_of(record)}


async def record_commit(
    db: AsyncSession,
    *,
    record: Any,
    knowledge_base_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    result: dict[str, Any],
) -> None:
    """Write what a publish did, per sheet, wherever it ran.

    A version is the thing every subsequent answer is computed from, so "an
    import replaced the numbers" belongs in the trail with its counts rather than
    only in the dataset's own history - whether a request or a worker did it.
    """
    await record_audit(
        db,
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        action="dataset.import_committed",
        target_type="knowledge_base",
        target_id=str(knowledge_base_id),
        details={
            "import_id": str(record.id),
            "filename": record.filename,
            "status": result.get("status"),
            "sheets": [
                {
                    "sheet_name": sheet.get("sheet_name"),
                    "dataset_id": sheet.get("dataset_id"),
                    "version_no": sheet.get("version_no"),
                    "rows_added": sheet.get("rows_added"),
                    "rows_changed": sheet.get("rows_changed"),
                    "rows_removed": sheet.get("rows_removed"),
                }
                for sheet in result.get("sheets", [])
            ],
        },
    )


async def give_up(db: AsyncSession, record: Any, why: str) -> None:
    """Mark an accepted import that no worker finished, so it can be retried.

    A process that dies mid-import leaves the row `running`; without this the
    poller would wait on it forever and the staged workbook would never be
    released. `failed` is the status `commit` accepts again, so the recovery
    path is the same button the person already has.
    """
    record.status = "failed"
    record.error = f"The worker running this import stopped: {why}"[:1000]
    record.summary = _summary(
        total=int((record.summary or {}).get("total") or 0),
        sheets=list((record.summary or {}).get("sheets") or []),
        failures=int((record.summary or {}).get("failures") or 0),
        current=None,
        phase="done",
    )
    await db.commit()


def read_sheet_proposal(payload: dict[str, Any]) -> SheetProposal:
    return SheetProposal.model_validate(payload)
