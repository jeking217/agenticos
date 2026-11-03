"""Request and response shapes for dataset knowledge bases.

The model-facing half (`DatasetQuery`) is deliberately a *description* of a
query rather than SQL: the compiler in `app.services.kb_dataset.query` is the
only thing that writes SQL, and it can only write what these shapes describe.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import BaseSchema, TimestampSchema

FieldType = Literal["text", "integer", "numeric", "date", "timestamp", "boolean"]
StorageMode = Literal["pooled", "dedicated", "archived"]
VersionKind = Literal["import", "maintenance", "model", "restore"]
Cardinality = Literal["1:1", "1:n", "n:1"]
RelationStatus = Literal["confirmed", "candidate", "disabled"]
ImportStatus = Literal["draft", "preview", "queued", "running", "committed", "partial", "failed"]


class DatasetFieldInput(BaseSchema):
    # A field name is a JSON key and a result alias, never a SQL identifier; it
    # may therefore be Chinese, which is what most source headers are. Quotes and
    # control characters are refused because they would need escaping in both.
    # `\z` rather than `$`: `$` also matches before a trailing newline, which
    # would let `金额\n` in as a field distinct from `金额` (T32).
    name: str = Field(min_length=1, max_length=64, pattern=r"^[^\s\x00-\x1f\"']+\z")
    display_name: str | None = Field(default=None, max_length=128)
    data_type: FieldType = "text"
    is_nullable: bool = True
    is_primary_key: bool = False
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    format_hint: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=500)


class DatasetFieldRead(BaseSchema):
    name: str
    display_name: str
    data_type: FieldType
    ordinal: int
    is_nullable: bool
    is_primary_key: bool
    synonyms: list[str] = Field(default_factory=list)
    format_hint: str | None = None
    description: str | None = None


class DatasetMetricInput(BaseSchema):
    name: str = Field(min_length=1, max_length=64, pattern=r'^[^\s\x00-\x1f"\']+$')
    display_name: str | None = Field(default=None, max_length=128)
    agg: Literal["sum", "avg", "min", "max", "count", "count_distinct"] = "sum"
    field_name: str | None = Field(default=None, max_length=64)
    format_hint: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=500)


class DatasetMetricRead(BaseSchema):
    name: str
    display_name: str
    agg: str
    field_name: str | None = None
    format_hint: str | None = None
    description: str | None = None


class DatasetCreate(BaseSchema):
    """A dataset is created from a workbook or by hand, but always with a model.

    `fields` is required when `import_id` is absent: the whole point of a data
    knowledge base is that the shape is agreed before rows arrive.
    """

    knowledge_base_id: UUID
    name: str = Field(min_length=1, max_length=128)
    sheet_name: str | None = Field(default=None, max_length=255)
    source_filename: str | None = Field(default=None, max_length=255)
    fields: list[DatasetFieldInput] = Field(min_length=1, max_length=200)
    metrics: list[DatasetMetricInput] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one_primary_key(self) -> DatasetCreate:
        keys = [f for f in self.fields if f.is_primary_key]
        if not keys:
            raise ValueError("A dataset needs at least one primary-key field")
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("Field names must be unique")
        metric_names = [m.name for m in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("Metric names must be unique")
        return self


class DatasetUpdate(BaseSchema):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class DatasetRead(BaseSchema, TimestampSchema):
    id: UUID
    knowledge_base_id: UUID
    organization_id: UUID
    name: str
    sheet_name: str | None = None
    source_filename: str | None = None
    primary_key_fields: list[str] = Field(default_factory=list)
    current_version_no: int = 0
    model_version: int = 1
    storage_mode: StorageMode = "pooled"
    row_count: int = 0
    revision: int = 1
    fields: list[DatasetFieldRead] = Field(default_factory=list)
    metrics: list[DatasetMetricRead] = Field(default_factory=list)
    last_import_at: datetime | None = None


class DatasetList(BaseSchema):
    items: list[DatasetRead]
    total: int


class DatasetFieldChange(BaseSchema):
    """Replaces the whole field list and bumps the model version once."""

    fields: list[DatasetFieldInput] = Field(min_length=1, max_length=200)
    metrics: list[DatasetMetricInput] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _unique(self) -> DatasetFieldChange:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("Field names must be unique")
        if not any(f.is_primary_key for f in self.fields):
            raise ValueError("A dataset needs at least one primary-key field")
        return self


class DatasetRelationInput(BaseSchema):
    target_dataset_id: UUID
    name: str = Field(min_length=1, max_length=64, pattern=r'^[^\s\x00-\x1f"\']+$')
    cardinality: Cardinality = "n:1"
    join_type: Literal["inner", "left"] = "left"
    local_fields: list[str] = Field(min_length=1, max_length=8)
    foreign_fields: list[str] = Field(min_length=1, max_length=8)
    status: RelationStatus = "confirmed"
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _same_width(self) -> DatasetRelationInput:
        if len(self.local_fields) != len(self.foreign_fields):
            raise ValueError("Both sides of a relation need the same number of fields")
        return self


class DatasetRelationRead(BaseSchema, TimestampSchema):
    id: UUID
    dataset_id: UUID
    target_dataset_id: UUID
    target_dataset_name: str | None = None
    name: str
    cardinality: Cardinality
    join_type: str
    local_fields: list[str]
    foreign_fields: list[str]
    status: RelationStatus
    confidence: float | None = None
    description: str | None = None


class DatasetRelationList(BaseSchema):
    items: list[DatasetRelationRead]
    total: int


class DatasetVersionRead(BaseSchema):
    id: UUID
    dataset_id: UUID
    version_no: int
    kind: VersionKind
    source_filename: str | None = None
    sheet_name: str | None = None
    rows_added: int = 0
    rows_changed: int = 0
    rows_removed: int = 0
    rows_unchanged: int = 0
    note: str | None = None
    published_at: datetime
    published_by: UUID | None = None
    stats: dict[str, Any] = Field(default_factory=dict)


class DatasetVersionList(BaseSchema):
    items: list[DatasetVersionRead]
    total: int


class DatasetRowRead(BaseSchema):
    record_key: str
    record_version: int
    valid_from: int
    valid_to: int | None = None
    is_deleted: bool = False
    values: dict[str, Any] = Field(default_factory=dict)


class DatasetRowList(BaseSchema):
    items: list[DatasetRowRead]
    total: int
    version_no: int
    columns: list[DatasetFieldRead] = Field(default_factory=list)


# --- query DSL -------------------------------------------------------------


class Filter(BaseSchema):
    """A leaf predicate. `op` decides which of the value fields is read.

    `op` also decides which *part* of the query a leaf is answered in, and the
    compiler enforces it, so a filter cannot quietly read a column that has not
    been computed yet:

    - a source field is a WHERE on the rows, before grouping;
    - a window alias (`rank`, `share`, ...) is a WHERE on the wrapped result,
      because the value does not exist until the window layer has run.

    The four subquery operators read a `SubQuerySpec` by name instead of values:
    `in_subquery` / `not_in_subquery` compare one field against the one column
    that spec exposes, and `exists` / `not_exists` ask whether a row on the
    related dataset matches the field on this one.
    """

    field: str = Field(max_length=200, description="field, or alias.field for a joined dataset")
    op: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "like",
        "is_null",
        "between",
        "in_subquery",
        "not_in_subquery",
        "exists",
        "not_exists",
    ] = "eq"
    value: Any = None
    values: list[Any] | None = Field(default=None, max_length=500)
    subquery: str | None = Field(
        default=None, max_length=64, description="name of a entry in `subqueries`"
    )

    @model_validator(mode="after")
    def _a_subquery_operator_names_one(self) -> Filter:
        needs_one = self.op in _SUBQUERY_OPS
        if needs_one and not self.subquery:
            raise ValueError(f"`{self.op}` needs `subquery`, naming one from `subqueries`")
        if not needs_one and self.subquery:
            raise ValueError(f"`subquery` is only read by {sorted(_SUBQUERY_OPS)}")
        return self


_SUBQUERY_OPS: frozenset[str] = frozenset(
    {"in_subquery", "not_in_subquery", "exists", "not_exists"}
)


class SubQuerySpec(BaseSchema):
    """One derived table: a field of a related dataset, filtered, at a version.

    Deliberately not a full `DatasetQuery`. A subquery exposes exactly one column,
    because every operator that reads one compares against a single field - `IN`
    against a list, `EXISTS` against a correlation - and a second column could only
    be reached by a construct that names it, which is where "just a small join"
    turns into arbitrary SQL. `relation` has to be registered on the root dataset
    for the same reason `joins` does: the reachable set of datasets is what the
    model was granted, not what it can name.

    `subqueries` exists to be refused. `MAX_SUBQUERY_DEPTH` is 2 - the root and one
    level - and a spec that carries its own is what a caller writes when it wants a
    third. Refusing it with a limit in the message is better than a schema that
    silently accepts a shape the compiler will not compile.
    """

    name: str = Field(min_length=1, max_length=64, description="referenced by `subquery`")
    relation: str = Field(max_length=64, description="a relation registered on the root dataset")
    field: str = Field(max_length=200, description="the one column it exposes")
    filters: FilterNode | None = None
    limit: int = Field(default=1000, ge=1, description="rows kept; the compiler caps it")
    subqueries: list[SubQuerySpec] = Field(default_factory=list, max_length=3)


class FilterGroup(BaseSchema):
    and_: list[FilterNode] | None = Field(default=None, alias="and", max_length=50)
    or_: list[FilterNode] | None = Field(default=None, alias="or", max_length=50)

    @model_validator(mode="after")
    def _one_operator(self) -> FilterGroup:
        if bool(self.and_) == bool(self.or_):
            raise ValueError("A filter group takes exactly one of `and` or `or`")
        return self


FilterNode = Filter | FilterGroup

# `FilterGroup` names `FilterNode` before it exists; the alias is a forward
# reference until this line, and Pydantic will not build the schema without it.
FilterGroup.model_rebuild()
SubQuerySpec.model_rebuild()


class SelectItem(BaseSchema):
    field: str | None = Field(default=None, max_length=200)
    metric: str | None = Field(default=None, max_length=64)
    alias: str | None = Field(default=None, max_length=64, pattern=r'^[^\s\x00-\x1f"\']+$')

    @model_validator(mode="after")
    def _one_source(self) -> SelectItem:
        if bool(self.field) == bool(self.metric):
            raise ValueError("A select item is either a field or a metric")
        return self


class WindowSpec(BaseSchema):
    """A calculation over the grouped result, not over the base rows."""

    macro: Literal["mom", "yoy", "share", "cum", "rank", "topn"]
    value: str = Field(max_length=64, description="A selected field or metric alias")
    alias: str = Field(max_length=64, pattern=r'^[^\s\x00-\x1f"\']+$')
    partition_by: list[str] = Field(default_factory=list, max_length=8)
    order_by: str | None = Field(default=None, max_length=64)
    offset: int = Field(default=1, ge=1, le=120)
    # Omitted, a trend reads forwards in time and a rank reads biggest first -
    # which is what each macro means. Saying it explicitly overrides both.
    order: Literal["ascending", "descending"] | None = None
    n: int = Field(default=10, ge=1, le=100)


class OrderSpec(BaseSchema):
    field: str = Field(max_length=64)
    descending: bool = True


class JoinSpec(BaseSchema):
    relation: str = Field(max_length=64, description="A relation registered on the root dataset")
    alias: str | None = Field(default=None, max_length=32, pattern=r'^[^\s\x00-\x1f"\']+$')


class DatasetQuery(BaseSchema):
    """What the model may ask for. Nothing here is SQL, and nothing can be."""

    dataset: str = Field(max_length=128, description="Dataset name or id")
    fields: list[SelectItem] = Field(default_factory=list, max_length=50)
    filters: FilterNode | None = None
    group_by: list[str] = Field(default_factory=list, max_length=10)
    order_by: list[OrderSpec] = Field(default_factory=list, max_length=5)
    windows: list[WindowSpec] = Field(default_factory=list, max_length=5)
    joins: list[JoinSpec] = Field(default_factory=list, max_length=3)
    subqueries: list[SubQuerySpec] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=100, ge=1, le=1000)
    as_of_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _windows_need_aliases(self) -> DatasetQuery:
        selected = {item.alias or item.field or item.metric for item in self.fields}
        for window in self.windows:
            if window.value not in selected:
                raise ValueError(
                    f"Window `{window.alias}` reads `{window.value}`, which is not selected"
                )
        return self


class QueryColumn(BaseSchema):
    name: str
    data_type: str = "text"


class DatasetQueryResult(BaseSchema):
    columns: list[QueryColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    version_vector: dict[str, int] = Field(default_factory=dict)
    sql: str = Field(default="", description="The compiled statement, for audit rather than use")
    params: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class DatasetBaseCreate(BaseSchema):
    """A knowledge base that holds datasets rather than documents.

    It is the same row in `knowledge_bases` as a document base - same owner,
    visibility, grants and permissions - with `kb_type='dataset'` and no vector
    collection behind it.
    """

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    scope: Literal["personal", "org"] = "personal"


# --- versions and comparison ----------------------------------------------


class RowChange(BaseSchema):
    record_key: str
    change: Literal["added", "changed", "removed", "missing"]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    changed_fields: list[str] = Field(default_factory=list)


class DatasetCompare(BaseSchema):
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    limit: int = Field(default=200, ge=1, le=2000)


class DatasetCompareResult(BaseSchema):
    from_version: int
    to_version: int
    added: int = 0
    changed: int = 0
    removed: int = 0
    missing: int = 0
    unchanged: int = 0
    rows: list[RowChange] = Field(default_factory=list)
    truncated: bool = False


# --- maintenance -----------------------------------------------------------


class DatasetRowUpdate(BaseSchema):
    values: dict[str, Any] = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=500)
    expected_version: int | None = Field(default=None, ge=1)


class DatasetRowDelete(BaseSchema):
    reason: str = Field(min_length=3, max_length=500)
    expected_version: int | None = Field(default=None, ge=1)


class DatasetRowRestore(BaseSchema):
    reason: str = Field(min_length=3, max_length=500)


class DatasetOperationRead(BaseSchema):
    id: UUID
    version_no: int
    operation: str
    record_key: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    reason: str | None = None
    actor_user_id: UUID | None = None
    created_at: datetime


class DatasetOperationList(BaseSchema):
    items: list[DatasetOperationRead]
    total: int


# --- import ----------------------------------------------------------------


class SheetFieldProposal(BaseSchema):
    name: str
    display_name: str
    data_type: FieldType
    is_nullable: bool = True
    is_primary_key: bool = False
    distinct_ratio: float = 0.0
    samples: list[str] = Field(default_factory=list)


class SheetProposal(BaseSchema):
    sheet_name: str
    kind: Literal["data", "summary", "empty"] = "data"
    header_row: int | None = None
    row_count: int = 0
    column_count: int = 0
    fields: list[SheetFieldProposal] = Field(default_factory=list)
    preview: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    merged_cells: int = 0
    quality: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Null rates, type mismatches and the row numbers that cannot be "
            "published, from a sample of the sheet; the commit reports the whole of it"
        ),
    )


class DatasetImportRead(BaseSchema, TimestampSchema):
    id: UUID
    knowledge_base_id: UUID
    status: ImportStatus
    filename: str
    file_size: int = 0
    sheet_count: int = 0
    sheets: list[SheetProposal] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    committed_at: datetime | None = None


class ImportProgress(BaseSchema):
    """How far a publish got, which is the whole of what a poller needs."""

    phase: Literal["queued", "running", "done"]
    total: int = 0
    done: int = 0
    failed: int = 0
    current_sheet: str | None = None
    percent: int = 0


class DatasetImportAccepted(BaseSchema):
    """The answer to a commit: accepted, not yet published.

    `progress` is filled in from the queued row, so a caller that polls the
    detail endpoint never has to special-case the first response.
    """

    import_id: UUID
    status: ImportStatus
    progress: ImportProgress


class DatasetImportStatus(BaseSchema):
    """What a running import has done so far, sheet by sheet."""

    id: UUID
    knowledge_base_id: UUID
    status: ImportStatus
    filename: str
    sheet_count: int = 0
    progress: ImportProgress
    sheets: list[dict[str, Any]] = Field(default_factory=list)
    plan: DatasetImportPlan
    """The parsed workbook and the decisions saved against it: where to resume."""
    error: str | None = None
    committed_at: datetime | None = None


class DatasetImportSheet(BaseSchema):
    """One sheet's destination: an existing dataset or a new one."""

    sheet_name: str
    dataset_id: UUID | None = None
    dataset_name: str | None = Field(default=None, max_length=128)
    fields: list[DatasetFieldInput] | None = None
    primary_key_fields: list[str] | None = None
    on_invalid: Literal["block", "skip"] = Field(
        default="block",
        description=(
            "What to do with rows that have no identity - a repeated key, or none at "
            "all. `block` refuses the sheet and names the rows; `skip` publishes the "
            "rest and records how many were withheld"
        ),
    )


class DatasetImportCommit(BaseSchema):
    sheets: list[DatasetImportSheet] = Field(min_length=1, max_length=30)
    note: str | None = Field(default=None, max_length=500)


DraftStep = Literal["sheets", "model", "validate"]
"""Which of the wizard's steps the person was on. `publish` is the commit."""


class DatasetImportDraft(BaseSchema):
    """What the wizard has decided so far, saved so closing it loses nothing.

    A workbook of six sheets is a folder of decisions - which sheets, what each
    one is called, which column identifies a row - and the dialog is a place
    people leave halfway. Saving them here is what makes the flow resumable;
    nothing reaches a dataset until the commit.
    """

    step: DraftStep = "model"
    sheets: list[DatasetImportSheet] = Field(default_factory=list, max_length=30)
    note: str | None = Field(default=None, max_length=500)


class DatasetImportPlan(BaseSchema):
    """What the wizard reads to put itself back where the person left it."""

    filename: str
    step: Literal["sheets", "model", "validate", "publish"]
    sheets: list[dict[str, Any]] = Field(default_factory=list)
    """The parsed proposals, as `preview` returned them - one per sheet."""
    choices: DatasetImportDraft | None = None
    """The decisions saved at `step`, or `None` if none were saved yet."""
