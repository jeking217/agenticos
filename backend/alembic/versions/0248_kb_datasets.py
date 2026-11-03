"""A second kind of knowledge base: Excel-derived datasets, versioned by row.

Documents are read whole and replaced; a dataset is read by the row, and a row
has a history. That difference lives in the storage rather than in a flag: the
ten tables here hold intervals (`valid_from` / `valid_to`) rather than current
values, so a version is never an edit and an old answer is still reproducible.

The schema is separate (`kb_data`) for two reasons that both show up the day
after a mistake: a backup can name it (`pg_dump -n kb_data`), and a rescue
restore of `public` cannot silently take the data with it. Table names drop the
`kb_dataset_` prefix the application-level names keep, because the schema is
already the qualifier.

`dataset_rows` is partitioned by list on `partition_key`: one partition per
dataset once it is large enough to deserve one, two shared pools below that.
The partition key is carried in every row and in every primary key - Postgres
requires it - and every query names it, so the planner prunes to a single
partition instead of reading the tenant's whole history.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0248_kb_datasets"
down_revision: str | Sequence[str] | None = "0247_merge_deployment_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "kb_data"

# The two shared pools. A dataset below the promotion threshold keeps writing
# into one of them, chosen by a stable hash of its id; a promoted dataset gets
# its own partition keyed by the same id. Neither is a DEFAULT: a row whose key
# is nobody's would then land somewhere plausible and be found by no query,
# which is worse than the insert failing.
POOL_A = "00000000-0000-4000-8000-00000000a001"
POOL_B = "00000000-0000-4000-8000-00000000a002"

_GRANTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_ro') THEN
        GRANT USAGE ON SCHEMA {SCHEMA} TO app_ro;
        GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO app_ro;
        ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA}
            GRANT SELECT ON TABLES TO app_ro;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
        GRANT USAGE, CREATE ON SCHEMA {SCHEMA} TO app_rw;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {SCHEMA} TO app_rw;
        ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA}
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # Which kind of base this is. Document bases keep every existing behaviour;
    # nothing in the RAG path reads this column.
    op.add_column(
        "knowledge_bases",
        sa.Column("kb_type", sa.String(length=16), nullable=False, server_default="document"),
    )
    op.create_check_constraint(
        "ck_knowledge_bases_kb_type",
        "knowledge_bases",
        "kb_type IN ('document', 'dataset')",
    )

    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("primary_key_fields", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("current_version_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("storage_mode", sa.String(length=16), nullable=False, server_default="pooled"),
        sa.Column("storage_partition_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_import_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="datasets_pkey"),
        sa.UniqueConstraint("knowledge_base_id", name="datasets_knowledge_base_id_key"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["public.knowledge_bases.id"],
            name="datasets_knowledge_base_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["public.organizations.id"],
            name="datasets_organization_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "storage_mode IN ('pooled', 'dedicated', 'archived')",
            name="datasets_storage_mode_check",
        ),
        sa.CheckConstraint("current_version_no >= 0", name="datasets_current_version_no_check"),
        schema=SCHEMA,
    )
    op.create_index("datasets_organization_id_idx", "datasets", ["organization_id"], schema=SCHEMA)

    op.create_table(
        "dataset_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("data_type", sa.String(length=16), nullable=False),
        sa.Column("is_nullable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_primary_key", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("synonyms", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("format_hint", sa.String(length=32), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="dataset_fields_pkey"),
        sa.UniqueConstraint("dataset_id", "name", name="dataset_fields_dataset_id_name_key"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            [f"{SCHEMA}.datasets.id"],
            name="dataset_fields_dataset_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "data_type IN ('text', 'integer', 'numeric', 'date', 'timestamp', 'boolean')",
            name="dataset_fields_data_type_check",
        ),
        sa.CheckConstraint("ordinal >= 0", name="dataset_fields_ordinal_check"),
        schema=SCHEMA,
    )

    op.create_table(
        "dataset_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("agg", sa.String(length=16), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=True),
        sa.Column("expression", postgresql.JSONB(), nullable=True),
        sa.Column("format_hint", sa.String(length=32), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="dataset_metrics_pkey"),
        sa.UniqueConstraint("dataset_id", "name", name="dataset_metrics_dataset_id_name_key"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            [f"{SCHEMA}.datasets.id"],
            name="dataset_metrics_dataset_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "agg IN ('sum', 'avg', 'min', 'max', 'count', 'count_distinct')",
            name="dataset_metrics_agg_check",
        ),
        schema=SCHEMA,
    )

    # A snapshot of the field list at each model version, so a query made under
    # model v2 can still explain what v1 called a column.
    op.create_table(
        "dataset_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="dataset_models_pkey"),
        sa.UniqueConstraint("dataset_id", "version_no", name="dataset_models_dataset_id_version_no_key"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            [f"{SCHEMA}.datasets.id"],
            name="dataset_models_dataset_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version_no >= 1", name="dataset_models_version_no_check"),
        schema=SCHEMA,
    )

    op.create_table(
        "dataset_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("sheet_name", sa.String(length=255), nullable=True),
        sa.Column("import_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rows_added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("published_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="dataset_versions_pkey"),
        sa.UniqueConstraint("dataset_id", "version_no", name="dataset_versions_dataset_id_version_no_key"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            [f"{SCHEMA}.datasets.id"],
            name="dataset_versions_dataset_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('import', 'maintenance', 'model', 'restore')",
            name="dataset_versions_kind_check",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "dataset_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("cardinality", sa.String(length=8), nullable=False),
        sa.Column("join_type", sa.String(length=8), nullable=False, server_default="left"),
        sa.Column("local_fields", postgresql.JSONB(), nullable=False),
        sa.Column("foreign_fields", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="dataset_relations_pkey"),
        sa.UniqueConstraint("dataset_id", "name", name="dataset_relations_dataset_id_name_key"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            [f"{SCHEMA}.datasets.id"],
            name="dataset_relations_dataset_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_dataset_id"],
            [f"{SCHEMA}.datasets.id"],
            name="dataset_relations_target_dataset_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "cardinality IN ('1:1', '1:n', 'n:1')", name="dataset_relations_cardinality_check"
        ),
        sa.CheckConstraint("join_type IN ('inner', 'left')", name="dataset_relations_join_type_check"),
        sa.CheckConstraint(
            "status IN ('confirmed', 'candidate', 'disabled')",
            name="dataset_relations_status_check",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "dataset_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("file_digest", sa.String(length=64), nullable=True),
        # Where the uploaded workbook waits between preview and commit. It is a
        # file rather than a column because the second half of the flow parses
        # it again, and a copy in the database would have to be read whole.
        sa.Column("staged_path", sa.String(length=500), nullable=True),
        sa.Column("sheet_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="dataset_imports_pkey"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["public.knowledge_bases.id"],
            name="dataset_imports_knowledge_base_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["public.organizations.id"],
            name="dataset_imports_organization_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'preview', 'committed', 'partial', 'failed')",
            name="dataset_imports_status_check",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "dataset_imports_kb_created_idx",
        "dataset_imports",
        ["knowledge_base_id", "created_at"],
        schema=SCHEMA,
    )

    # A version somebody's answer was computed from. Retention never removes a
    # pinned version; the pin is what makes "the numbers I showed you last week"
    # still true this week.
    op.create_table(
        "dataset_version_pins",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="dataset_version_pins_pkey"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            [f"{SCHEMA}.datasets.id"],
            name="dataset_version_pins_dataset_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version_no >= 1", name="dataset_version_pins_version_no_check"),
        schema=SCHEMA,
    )
    op.create_index(
        "dataset_version_pins_dataset_version_idx",
        "dataset_version_pins",
        ["dataset_id", "version_no"],
        schema=SCHEMA,
    )

    op.create_table(
        "dataset_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("record_key", sa.String(length=512), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="dataset_operations_pkey"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            [f"{SCHEMA}.datasets.id"],
            name="dataset_operations_dataset_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "operation IN ('insert', 'update', 'delete', 'restore', 'import', 'model')",
            name="dataset_operations_operation_check",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "dataset_operations_dataset_created_idx",
        "dataset_operations",
        ["dataset_id", "created_at"],
        schema=SCHEMA,
    )

    # The interval store. Every write appends; nothing is updated in place, so
    # an as-of read at version v is `valid_from <= v AND (valid_to IS NULL OR
    # valid_to > v)` and a deletion is a tombstone row rather than a gap.
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.dataset_rows (
            partition_key uuid NOT NULL,
            id uuid NOT NULL,
            dataset_id uuid NOT NULL,
            record_key varchar(512) NOT NULL,
            record_version integer NOT NULL,
            valid_from integer NOT NULL,
            valid_to integer,
            is_deleted boolean NOT NULL DEFAULT false,
            values jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT dataset_rows_pkey PRIMARY KEY (partition_key, id),
            CONSTRAINT dataset_rows_dataset_id_fkey
                FOREIGN KEY (dataset_id) REFERENCES {SCHEMA}.datasets (id) ON DELETE CASCADE,
            CONSTRAINT dataset_rows_interval_check
                CHECK (valid_to IS NULL OR valid_to > valid_from),
            CONSTRAINT dataset_rows_record_version_check CHECK (record_version >= 1),
            CONSTRAINT dataset_rows_valid_from_check CHECK (valid_from >= 1),
            CONSTRAINT dataset_rows_values_object_check CHECK (jsonb_typeof(values) = 'object')
        ) PARTITION BY LIST (partition_key)
        """
    )
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.dataset_rows_pool_a
            PARTITION OF {SCHEMA}.dataset_rows FOR VALUES IN ('{POOL_A}')
        """
    )
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.dataset_rows_pool_b
            PARTITION OF {SCHEMA}.dataset_rows FOR VALUES IN ('{POOL_B}')
        """
    )

    # One open interval per record. `valid_to IS NULL` is the current row, so
    # this is the index that makes "read the latest" a point lookup rather than
    # a window over history.
    op.execute(
        f"""
        CREATE UNIQUE INDEX dataset_rows_open_uq
            ON {SCHEMA}.dataset_rows (partition_key, dataset_id, record_key)
            WHERE valid_to IS NULL
        """
    )
    op.execute(
        f"""
        CREATE INDEX dataset_rows_history_idx
            ON {SCHEMA}.dataset_rows (partition_key, dataset_id, record_key, valid_from)
        """
    )
    op.execute(
        f"""
        CREATE INDEX dataset_rows_values_idx
            ON {SCHEMA}.dataset_rows USING gin (values jsonb_path_ops)
        """
    )

    op.execute(_GRANTS)


def downgrade() -> None:
    # The schema holds only what this migration created, so dropping it whole is
    # exact rather than approximate.
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    op.drop_constraint("ck_knowledge_bases_kb_type", "knowledge_bases", type_="check")
    op.drop_column("knowledge_bases", "kb_type")
