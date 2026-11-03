"""The model that reads a workbook before anybody agrees with it.

The parser in `excel.py` guesses from shapes: a row that looks like a header, a
column whose values all parse as numbers, a column with no repeats that makes a
usable key. That is a good guess and it stops at the edge of what a shape can
tell you. It cannot know that `客户编号` and `客户编码` name the same thing, that
`金额` is yuan rather than cents, that a column of `1/2` means January and
February rather than a fraction, or that `订单明细` joins `订单` on `订单号` - and
those are exactly the decisions that make a dataset usable rather than merely
imported.

So this module asks a chat model, with three rules that keep the answer safe to
use:

**It is a suggestion, and every suggestion is checked against the workbook.**
:func:`apply` throws away advice about a sheet or a column that does not exist,
refuses a data type outside the closed vocabulary, and keeps exactly one primary
key. A model that hallucinates a column cannot therefore invent one.

**Nothing here can fail an import.** A provider that is down, a key that was
rotated, an answer that arrived malformed after its retries - all of it degrades
to "no advice", and the wizard carries on with the parser's proposal. The reason
travels with the outcome so the screen can say why the advice is missing.

**The prompt is a digest, not the workbook.** Column names, inferred types,
distinct ratios and a handful of sample values per column. A 40 MB workbook is
never sent anywhere, and a sheet with 500k rows costs the same as one with five.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from app.agents.model_resolver import ResolvedCredential, build_model, get_provider
from app.core.exceptions import BadRequestError
from app.core.secret_kinds import NoSecret, SecretKind, unseal_secret
from app.core.vault import VaultScope
from app.db.models.knowledge_base import KnowledgeBase
from app.repositories import organization_secret_repo
from app.services.kb_dataset import excel

logger = logging.getLogger(__name__)

FieldType = Literal["text", "integer", "numeric", "date", "timestamp", "boolean"]

_CARDINALITIES = frozenset({"1:1", "1:n", "n:1"})
_KINDS = frozenset({"data", "summary", "empty"})
_FIELD_TYPES = frozenset({"text", "integer", "numeric", "date", "timestamp", "boolean"})

_OUTPUT_RETRIES = 1
"""How often an answer that failed validation may be corrected."""

_LIMITS = UsageLimits(request_limit=_OUTPUT_RETRIES + 1)
"""The run's own ceiling: the answer plus each corrected attempt."""

_FAILURES = (ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded)
"""What the call may fail with that is no defect of this code.

Anything else - a bug, a cancellation - propagates. The caller catches
`BadRequestError` too, which is how "no provider configured" and "that key is
gone" arrive: they are answers to a question somebody can act on, not crashes.
"""

_MAX_SHEETS = 20
"""Sheets described in the prompt. A workbook with more is read in its first 20."""

_MAX_COLUMNS = 60
"""Columns described per sheet; beyond this the digest stops being a digest."""

_MAX_SAMPLES = 4
"""Values shown per column. Enough to see a format, few enough to stay small."""

_MAX_PROMPT_CHARS = 24_000
"""A hard stop on the digest. Every real workbook lands far below it."""


class FieldAdvice(BaseModel):
    """What the model thinks one column is."""

    name: str = Field(description="The column's header exactly as given")
    data_type: FieldType = Field(description="The narrowest type that holds every value")
    is_nullable: bool | None = Field(
        default=None, description="True when blanks are expected in this column"
    )
    is_primary_key: bool = Field(
        default=False, description="True for the one column that identifies a row"
    )
    reason: str | None = Field(default=None, description="One clause, in the sheet's language")


class SheetAdvice(BaseModel):
    """What the model thinks one sheet is."""

    sheet_name: str = Field(description="The sheet's tab name exactly as given")
    kind: Literal["data", "summary", "empty"] | None = Field(
        default=None, description="`summary` for a totals/index/notes tab"
    )
    fields: list[FieldAdvice] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RelationAdvice(BaseModel):
    """A join the model believes exists between two sheets - or a sheet and a dataset."""

    from_sheet: str
    from_fields: list[str] = Field(min_length=1)
    to_sheet: str
    to_fields: list[str] = Field(min_length=1)
    cardinality: Literal["1:1", "1:n", "n:1"] = "n:1"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str | None = None


class Advice(BaseModel):
    """The whole answer: one entry per sheet, plus the joins between them."""

    sheets: list[SheetAdvice] = Field(default_factory=list)
    relations: list[RelationAdvice] = Field(default_factory=list)


@dataclass
class Outcome:
    """What came back, and why it is empty when it is.

    `reason` is None when the advice ran. Otherwise it is the sentence the screen
    shows - "no model is configured", "the key was refused" - because an advice
    section that is quietly missing is indistinguishable from one that failed.
    """

    advice: Advice | None = None
    reason: str | None = None
    provider: str | None = None
    model: str | None = None

    @property
    def ran(self) -> bool:
        return self.advice is not None


@dataclass
class Applied:
    """Advice merged into the proposals, and what it actually changed.

    The proposals are the same objects the parser produced, corrected in place:
    `apply` is the only place a suggestion becomes a field's type, so a caller
    cannot accidentally keep half of one.
    """

    proposals: list[excel.SheetProposal]
    sheets: set[str] = field(default_factory=set)
    """Sheets whose kind the model spoke about."""

    fields: dict[str, set[str]] = field(default_factory=dict)
    """Per sheet, the columns whose type or key the model decided."""

    relations: list[dict[str, Any]] = field(default_factory=list)
    """Suggestions naming only sheets and columns that exist, ready for the wizard."""

    notes: list[str] = field(default_factory=list)


def configured(kb: KnowledgeBase) -> bool:
    """Whether this base has a model at all - the one thing /analyze checks first."""
    return bool(kb.reasoning_provider and kb.reasoning_model)


async def build(db: Any, kb: KnowledgeBase) -> Model | None:
    """The base's model, built from the provider, the model id and the credential.

    Returns None when the base names no model, which is not an error: it is a
    base that imports on the parser's rules alone. Raises `BadRequestError` when
    it names one that cannot be built - a provider this build no longer offers, a
    key that has since been deleted or was stored for another provider - because
    those are sentences with a remedy, and the screen shows them as such.
    """
    if not configured(kb):
        return None
    assert kb.reasoning_provider is not None  # narrowed by `configured`
    assert kb.reasoning_model is not None
    spec = get_provider(kb.reasoning_provider)
    secret: Any = NoSecret()
    if kb.reasoning_secret_id is not None:
        if kb.organization_id is None:
            raise BadRequestError(
                message="This base belongs to no organization, so it has no vault to take a key from"
            )
        row = await organization_secret_repo.get(
            db, kb.reasoning_secret_id, organization_id=kb.organization_id
        )
        if row is None:
            raise BadRequestError(
                message=(
                    f"The key this base reads workbooks with is no longer in the vault; "
                    f"add a key for {spec.name} or choose another model"
                )
            )
        secret = unseal_secret(
            row.sealed_secret,
            kind=SecretKind(row.kind),
            scope=VaultScope.organization(kb.organization_id),
            key_version=row.key_version,
        )
    credential = ResolvedCredential(
        provider=kb.reasoning_provider,
        secret=secret,
        base_url=kb.reasoning_base_url,
    )
    return build_model(credential, kb.reasoning_model)


def _samples(proposal: excel.SheetProposal, column: excel.FieldProposal) -> list[str]:
    return list(column.samples[:_MAX_SAMPLES])


def digest(
    proposals: list[excel.SheetProposal],
    existing: list[dict[str, Any]] | None = None,
) -> str:
    """The workbook as the prompt sees it: names, shapes and a few values.

    Deliberately not the rows. A column's meaning is legible from its header, its
    inferred type and four values; sending the data would cost the customer's
    bandwidth to learn what the header already said, and would make the cost of
    an import scale with its size.
    """
    sheets: list[dict[str, Any]] = []
    for proposal in proposals[:_MAX_SHEETS]:
        sheet = proposal.sheet
        sheets.append(
            {
                "sheet": sheet.name,
                "looks_like": sheet.kind,
                "header_row": sheet.header_row,
                "rows": len(sheet.rows),
                "columns": [
                    {
                        "name": column.name,
                        "header": column.display_name,
                        "parsed_as": column.data_type,
                        "blanks": column.is_nullable,
                        "unique_values": f"{round(column.distinct_ratio * 100)}%",
                        "examples": _samples(proposal, column),
                    }
                    for column in proposal.fields[:_MAX_COLUMNS]
                ],
            }
        )
    payload = {"workbook": sheets, "existing_datasets": existing or []}
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > _MAX_PROMPT_CHARS:
        # Cut columns, not characters: half a JSON document asks a question
        # nobody can answer. Trimming the tail keeps every sheet's first columns.
        for proposal in proposals:
            proposal.fields = proposal.fields[:10]
        return digest(proposals, existing)
    return text


def _instructions(language: str) -> str:
    return (
        "You read spreadsheets for a data-import tool. For each sheet you are given its tab "
        "name, the parser's guess at the header row, and one entry per column: the field name, "
        "the header text, the type the parser inferred, whether blanks occur, how unique the "
        "column is, and a few example values.\n\n"
        "Answer with:\n"
        "1. `kind` per sheet - `data` for a table of records, `summary` for a totals block, a "
        "cover page, an index or a notes tab, `empty` for nothing.\n"
        "2. `data_type` for **every** column that is not obviously right, using only `text`, "
        "`integer`, `numeric`, `date`, `timestamp`, `boolean`. Look at the examples: `2024-01` "
        "is text even though it looks numeric, a 13-digit column of ids is text, a column of "
        "`是/否` is boolean. When the parser is right, say so by repeating its type.\n"
        "3. `is_primary_key` on exactly one column per `data` sheet: the one that identifies a "
        "record on its own (an order number, a code, a composite like date+channel is NOT a "
        "single column - choose the most specific unique one). If no column can, mark none.\n"
        "4. `relations`: joins between two sheets in this workbook, or between a sheet and an "
        "existing dataset, when the values plausibly match - a code, a name, a key. State both "
        "sides' column names, the cardinality, and a confidence from 0 to 1. Do not invent a "
        "join from a shared word alone; say nothing when unsure.\n\n"
        f"Write `reason` and `notes` in {language}, one short clause each. Name sheets and "
        "columns exactly as they were given - a renamed one will be ignored."
    )


async def advise(
    db: Any,
    kb: KnowledgeBase,
    proposals: list[excel.SheetProposal],
    *,
    existing: list[dict[str, Any]] | None = None,
    language: str = "the language of the sheet headers",
) -> Outcome:
    """Ask the base's model what the sheets mean; never raise for a bad answer."""
    if not configured(kb):
        return Outcome(reason="no_model")
    try:
        model = await build(db, kb)
    except BadRequestError as exc:
        return Outcome(reason=exc.message, provider=kb.reasoning_provider, model=kb.reasoning_model)
    if model is None:  # pragma: no cover - `configured` above says otherwise
        return Outcome(reason="no_model")

    agent: Agent[None, Advice] = Agent(
        model,
        instructions=_instructions(language),
        output_type=Advice,
        retries=_OUTPUT_RETRIES,
        name="dataset_reader",
    )
    try:
        result = await agent.run(digest(proposals, existing), usage_limits=_LIMITS)
    except _FAILURES:
        logger.warning("dataset_advice_failed", exc_info=True)
        return Outcome(
            reason="the model did not answer",
            provider=kb.reasoning_provider,
            model=kb.reasoning_model,
        )
    except BadRequestError as exc:
        return Outcome(reason=exc.message, provider=kb.reasoning_provider, model=kb.reasoning_model)
    return Outcome(
        advice=result.output,
        provider=kb.reasoning_provider,
        model=kb.reasoning_model,
    )


def _advice_for(sheets: list[SheetAdvice], name: str) -> SheetAdvice | None:
    """The entry for a sheet, matched exactly first and case-insensitively after.

    A model that lower-cases a tab name is making a cosmetic mistake, not a
    different claim; ignoring its whole answer over one would be throwing away
    the useful half.
    """
    for entry in sheets:
        if entry.sheet_name == name:
            return entry
    normalized = name.strip().casefold()
    for entry in sheets:
        if entry.sheet_name.strip().casefold() == normalized:
            return entry
    return None


def _field_advice(entries: list[FieldAdvice], name: str) -> FieldAdvice | None:
    for entry in entries:
        if entry.name == name:
            return entry
    normalized = name.strip().casefold()
    for entry in entries:
        if entry.name.strip().casefold() == normalized:
            return entry
    return None


def apply(proposals: list[excel.SheetProposal], advice: Advice) -> Applied:
    """Merge an answer into the parser's proposals, keeping only what checks out.

    Three rules, and each is a way a wrong answer stays harmless:

    - a sheet or column the workbook does not have is dropped, not added;
    - `data_type` must be one of the six the model can hold, so a model inventing
      `currency` cannot reach the database;
    - at most one primary key survives, because a record's identity is one
      decision and two of them is a key nobody can predict.

    Everything not mentioned keeps the parser's answer, so a partial reply is a
    partial improvement rather than a reset.
    """
    applied = Applied(proposals=proposals)
    by_name = {proposal.sheet.name: proposal for proposal in proposals}

    for entry in advice.sheets:
        proposal = by_name.get(entry.sheet_name) or next(
            (
                candidate
                for name, candidate in by_name.items()
                if name.strip().casefold() == entry.sheet_name.strip().casefold()
            ),
            None,
        )
        if proposal is None or proposal.sheet.kind == "empty":
            continue
        if entry.kind is not None and entry.kind in _KINDS and entry.kind != proposal.sheet.kind:
            proposal.sheet.kind = entry.kind
            applied.sheets.add(proposal.sheet.name)
        if proposal.sheet.kind != "data":
            continue

        changed: set[str] = set()
        for column in proposal.fields:
            suggested = _field_advice(entry.fields, column.name)
            if suggested is None:
                continue
            if suggested.data_type not in _FIELD_TYPES:
                continue
            if suggested.data_type != column.data_type:
                column.data_type = suggested.data_type
                changed.add(column.name)
            if suggested.is_nullable is not None and suggested.is_nullable != column.is_nullable:
                column.is_nullable = suggested.is_nullable
                changed.add(column.name)

        # The key is decided after the types, because a model that changes a
        # column to integer may also be the one naming it as the key.
        keys = [
            field.name
            for field in entry.fields
            if field.is_primary_key and any(column.name == field.name for column in proposal.fields)
        ]
        if keys:
            chosen = keys[0]
            for column in proposal.fields:
                wanted = column.name == chosen
                if column.is_primary_key != wanted:
                    column.is_primary_key = wanted
                    changed.add(column.name)
            proposal.primary_key = [chosen]
        if changed:
            applied.fields[proposal.sheet.name] = changed
        if changed:
            # The preview is a typed table, so a column the model retyped has to
            # be read again or the screen shows a date as the text it was.
            proposal.preview = excel.coerce_rows(
                proposal.sheet,
                {column.name: column.data_type for column in proposal.fields},
                limit=5,
            )

    applied.relations = _known_relations(proposals, advice.relations)
    return applied


def _known_relations(
    proposals: list[excel.SheetProposal], relations: list[RelationAdvice]
) -> list[dict[str, Any]]:
    """The suggestions that name things which exist, as the wizard's shapes.

    A relation is the one claim that reaches the database as a join the compiler
    will honour, so a pair of column names that do not exist is dropped here
    rather than refused at publish: the answer to "the model was wrong about a
    column" is to show no suggestion, not to fail the import.
    """
    columns = {
        proposal.sheet.name: {column.name for column in proposal.fields} for proposal in proposals
    }
    kept: list[dict[str, Any]] = []
    for entry in relations:
        if entry.from_sheet == entry.to_sheet:
            continue
        left = columns.get(entry.from_sheet)
        right = columns.get(entry.to_sheet)
        if left is None or right is None:
            continue
        if len(entry.from_fields) != len(entry.to_fields) or not entry.from_fields:
            continue
        if any(name not in left for name in entry.from_fields):
            continue
        if any(name not in right for name in entry.to_fields):
            continue
        kept.append(
            {
                "from_sheet": entry.from_sheet,
                "from_fields": list(entry.from_fields),
                "to_sheet": entry.to_sheet,
                "to_fields": list(entry.to_fields),
                "cardinality": entry.cardinality if entry.cardinality in _CARDINALITIES else "n:1",
                "confidence": round(entry.confidence, 3),
                "reason": entry.reason,
            }
        )
    return kept
