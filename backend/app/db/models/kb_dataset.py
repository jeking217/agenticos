"""Excel-derived datasets: intervals, not current values.

A document base replaces a file; a dataset keeps every row it has ever had. The
tables here are shaped by that one decision:

- `dataset_rows` is the history, append-only. A row is valid over
  `[valid_from, valid_to)` in version numbers, so "what did this look like at
  v3" is a range scan rather than a reconstruction from diffs. Deleting a record
  writes a tombstone interval; nothing is removed, because a version that
  removed it is still an answer somebody may ask for.
- `dataset_versions` names those versions. `record_version` on a row is the
  version in which *that row* last changed, and is deliberately not the dataset
  version: a row absent from v4 keeps `record_version = 3` and is explained by
  absence rather than by a fabricated change.
- `dataset_models` snapshots the field list per model version, so an old query
  can still be explained in the vocabulary it was asked in.

Everything lives in `kb_data` rather than `public`: it is backed up by name,
restored by name, and granted read-only to reporting roles by name.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

SCHEMA = "kb_data"

# Foreign keys that point at `public` tables are spelled without the schema.
# SQLAlchemy resolves one by looking its key up in `MetaData.tables`, and a
# `public` table registers under `organizations`, not `public.organizations` -
# so the qualified spelling finds nothing and `create_all`, which is how the
# integration fixture builds the schema, raises `NoReferencedTableError` for
# every test in the module. The DDL that reaches Postgres is untouched by this:
# `0248` creates these constraints with an explicit `referent_schema="public"`,
# and rule R10 is about what the database holds, not how the ORM names it in
# memory.

# The two shared partitions. Kept here as well as in the migration because the
# writer has to choose one, and a value that exists in only one of the two
# places is a row nobody can read.
POOL_A = uuid.UUID("00000000-0000-4000-8000-00000000a001")
POOL_B = uuid.UUID("00000000-0000-4000-8000-00000000a002")

DATA_TYPES = ("text", "integer", "numeric", "date", "timestamp", "boolean")
METRIC_AGGS = ("sum", "avg", "min", "max", "count", "count_distinct")
CARDINALITIES = ("1:1", "1:n", "n:1")


def pool_for(dataset_id: uuid.UUID) -> uuid.UUID:
    """The shared pool a dataset below the promotion threshold writes into.

    Stable across processes and restarts - a dataset that moved pools between
    two imports would leave half its history in a partition no query looks at.
    """
    return POOL_A if dataset_id.int % 2 == 0 else POOL_B


class Dataset(TimestampMixin, Base):
    """One Excel sheet promoted to a queryable, versioned table."""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_key_fields: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    storage_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="pooled")
    storage_partition_key: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_import_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Names, not the base: a workbook's sheets are several datasets sharing
        # one knowledge base, and a join is only possible between two of them.
        # Uniqueness is per base so two datasets in it can be told apart - which
        # is what `find_dataset` and the compiler's relation lookup assume.
        UniqueConstraint("knowledge_base_id", "name", name="datasets_knowledge_base_id_name_key"),
        CheckConstraint(
            "storage_mode IN ('pooled', 'dedicated', 'archived')",
            name="datasets_storage_mode_check",
        ),
        CheckConstraint("current_version_no >= 0", name="datasets_current_version_no_check"),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<Dataset(id={self.id}, name={self.name!r}, v={self.current_version_no})>"


class DatasetField(TimestampMixin, Base):
    """One column of a dataset, in the vocabulary the model may use."""

    __tablename__ = "dataset_fields"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    data_type: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    is_nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    synonyms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    format_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="dataset_fields_dataset_id_name_key"),
        CheckConstraint(
            "data_type IN ('text', 'integer', 'numeric', 'date', 'timestamp', 'boolean')",
            name="dataset_fields_data_type_check",
        ),
        CheckConstraint("ordinal >= 0", name="dataset_fields_ordinal_check"),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<DatasetField({self.dataset_id}.{self.name}:{self.data_type})>"


class DatasetMetric(TimestampMixin, Base):
    """A named aggregate, so "revenue" means one thing in every question."""

    __tablename__ = "dataset_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agg: Mapped[str] = mapped_column(String(16), nullable=False, default="sum")
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expression: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    format_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="dataset_metrics_dataset_id_name_key"),
        CheckConstraint(
            "agg IN ('sum', 'avg', 'min', 'max', 'count', 'count_distinct')",
            name="dataset_metrics_agg_check",
        ),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<DatasetMetric({self.dataset_id}.{self.name})>"


class DatasetModel(Base):
    """The field list as it stood at one model version."""

    __tablename__ = "dataset_models"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    fields: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    metrics: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("dataset_id", "version_no", name="dataset_models_dataset_id_version_no_key"),
        CheckConstraint("version_no >= 1", name="dataset_models_version_no_check"),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<DatasetModel({self.dataset_id}@v{self.version_no})>"


class DatasetVersion(Base):
    """A published version: what changed, from where, by whom."""

    __tablename__ = "dataset_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="import")
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    rows_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_removed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("dataset_id", "version_no", name="dataset_versions_dataset_id_version_no_key"),
        CheckConstraint(
            "kind IN ('import', 'maintenance', 'model', 'restore')",
            name="dataset_versions_kind_check",
        ),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<DatasetVersion({self.dataset_id}@v{self.version_no}:{self.kind})>"


class DatasetRow(Base):
    """One interval of one record.

    `values` holds the row as imported, including the key fields: a projection
    that reconstructed them from `record_key` would lose the original casing and
    whitespace, which is exactly what a comparison against the source file is
    looking for.
    """

    __tablename__ = "dataset_rows"

    partition_key: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "dataset_rows_open_uq",
            "partition_key",
            "dataset_id",
            "record_key",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
        Index(
            "dataset_rows_history_idx",
            "partition_key",
            "dataset_id",
            "record_key",
            "valid_from",
        ),
        Index(
            "dataset_rows_values_idx",
            "values",
            postgresql_using="gin",
            postgresql_ops={"values": "jsonb_path_ops"},
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="dataset_rows_interval_check"
        ),
        CheckConstraint("record_version >= 1", name="dataset_rows_record_version_check"),
        CheckConstraint("valid_from >= 1", name="dataset_rows_valid_from_check"),
        CheckConstraint("jsonb_typeof(values) = 'object'", name="dataset_rows_values_object_check"),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<DatasetRow({self.record_key}@{self.valid_from}..{self.valid_to})>"


class DatasetRelation(TimestampMixin, Base):
    """A join the model is allowed to make, and the only one it is allowed to make.

    The timestamps are the canvas's: an edge drawn by hand and one inferred from
    the data are otherwise indistinguishable once both are confirmed, and the
    relation read model carries them. The columns are in the migration; the mixin
    was simply left off the class, which made every read of one an
    `AttributeError` (#T29).
    """

    __tablename__ = "dataset_relations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_dataset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    cardinality: Mapped[str] = mapped_column(String(8), nullable=False, default="n:1")
    join_type: Mapped[str] = mapped_column(String(8), nullable=False, default="left")
    local_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    foreign_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="dataset_relations_dataset_id_name_key"),
        CheckConstraint(
            "cardinality IN ('1:1', '1:n', 'n:1')", name="dataset_relations_cardinality_check"
        ),
        CheckConstraint("join_type IN ('inner', 'left')", name="dataset_relations_join_type_check"),
        CheckConstraint(
            "status IN ('confirmed', 'candidate', 'disabled')",
            name="dataset_relations_status_check",
        ),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<DatasetRelation({self.name}:{self.dataset_id}->{self.target_dataset_id})>"


class DatasetImport(TimestampMixin, Base):
    """One uploaded workbook and the plan derived from it, sheet by sheet."""

    __tablename__ = "dataset_imports"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    file_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    staged_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sheet_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plan: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'preview', 'queued', 'running', 'committed', 'partial', 'failed')",
            name="dataset_imports_status_check",
        ),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<DatasetImport({self.filename!r}:{self.status})>"


class DatasetVersionPin(Base):
    """A version an answer was computed from, kept alive past retention."""

    __tablename__ = "dataset_version_pins"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("dataset_version_pins_dataset_version_idx", "dataset_id", "version_no"),
        CheckConstraint("version_no >= 1", name="dataset_version_pins_version_no_check"),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<DatasetVersionPin({self.dataset_id}@v{self.version_no})>"


class DatasetOperation(Base):
    """A maintenance action, with the reason somebody gave for it."""

    __tablename__ = "dataset_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    record_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("dataset_operations_dataset_created_idx", "dataset_id", "created_at"),
        CheckConstraint(
            "operation IN ('insert', 'update', 'delete', 'restore', 'import', 'model')",
            name="dataset_operations_operation_check",
        ),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<DatasetOperation({self.operation}:{self.record_key})>"
