"""What an operator can assert about a dataset deployment, and how.

The migration creates a second schema, a partitioned parent and two pooled
partitions by raw DDL, and grants to two roles that may or may not exist. None of
that is exercised by the unit suite - it builds the schema from models, where
`dataset_rows` is an ordinary table - so the first time a real deployment runs the
partitioned shape is on the day somebody restores a backup into it.

K12 in the plan is exactly that risk: an independent schema quietly left out of
the backup, or a partition count walking into the planner's ceiling, discovered
only when it matters. This module is the offline half. `check` refuses a
deployment whose shape is not what the code assumes; `verify-backup` compares a
dump's object list against the live catalog, which is the only way to catch a
backup that has been missing a schema for months.

Both are decisions over what a probe returned. `probe` is the only part that
talks to the database, which is what keeps the decision table testable without
one.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sql_text

SCHEMA = "kb_data"

# The ten tables 0248 creates, in the order the migration declares them. Spelled
# out rather than imported from the models: this is the check that the migration
# and the models agree, so it cannot be derived from either.
TABLES: tuple[str, ...] = (
    "datasets",
    "dataset_fields",
    "dataset_metrics",
    "dataset_models",
    "dataset_versions",
    "dataset_relations",
    "dataset_imports",
    "dataset_version_pins",
    "dataset_operations",
    "dataset_rows",
)

POOL_PARTITIONS: tuple[str, ...] = ("dataset_rows_pool_a", "dataset_rows_pool_b")

# Postgres plans fine well past this, but every partition is a lock, a relcache
# entry and a line in every autovacuum cycle, and the plan's promotion rule stops
# creating dedicated ones long before. The check fails at the ceiling rather than
# warning: a deployment that reached it cannot promote anything else.
PARTITION_LIMIT = 2000

# The role the agent's query path connects as. `app_rw` is the maintainer path.
READ_ROLE = "app_ro"
WRITE_ROLE = "app_rw"


@dataclass(frozen=True)
class Finding:
    """One assertion, with the sentence a person reads when it fails."""

    key: str
    title: str
    ok: bool
    detail: str
    # A non-fatal finding is a warning: it is reported, and it does not refuse the
    # deployment. Absent roles are the case - every local setup connects as the
    # owner, and there is no query path to protect there.
    fatal: bool = True


def verdict(findings: list[Finding]) -> int:
    """The process exit code: 1 when anything fatal failed, else 0."""

    return 1 if any(finding.fatal and not finding.ok for finding in findings) else 0


def evaluate(
    *,
    tables: set[str],
    partitions: set[str],
    rows_is_partitioned: bool,
    role_privileges: dict[str, dict[str, bool]],
    partition_count: int,
    schema_exists: bool = True,
) -> list[Finding]:
    """The whole check, as a decision over what the probe found.

    Ordered from the outside in: the schema, then its tables, then the thing the
    tables depend on, then the grants, then capacity. A person reading the output
    of a broken deployment fixes the first failure and re-runs.
    """

    findings: list[Finding] = [
        Finding(
            key="schema",
            title=f"schema {SCHEMA} exists",
            ok=schema_exists,
            detail=(
                f"{SCHEMA} is present"
                if schema_exists
                else f"{SCHEMA} is missing: run `alembic upgrade head`, and check that the "
                "backup and restore tooling includes non-public schemas"
            ),
        )
    ]

    missing = [table for table in TABLES if table not in tables]
    findings.append(
        Finding(
            key="tables",
            title=f"{len(TABLES)} tables",
            ok=not missing,
            detail=(
                f"all {len(TABLES)} tables present"
                if not missing
                else f"missing: {', '.join(missing)}"
            ),
        )
    )

    missing_partitions = [name for name in POOL_PARTITIONS if name not in partitions]
    partition_problems = []
    if missing_partitions:
        partition_problems.append(f"missing: {', '.join(missing_partitions)}")
    if not rows_is_partitioned:
        # The parent exists as a plain table when the raw DDL half of 0248 did not
        # run. Rows still insert, so nothing else notices until a promotion has no
        # partition to move.
        partition_problems.append("dataset_rows is not a partitioned table")
    findings.append(
        Finding(
            key="partitions",
            title="pool partitions attached",
            ok=not partition_problems,
            detail="; ".join(partition_problems) or "both pool partitions attached",
        )
    )

    findings.append(_grants_finding(role_privileges))

    findings.append(
        Finding(
            key="partition-count",
            title=f"partition count below {PARTITION_LIMIT}",
            ok=partition_count < PARTITION_LIMIT,
            detail=(
                f"{partition_count} partitions"
                if partition_count < PARTITION_LIMIT
                else f"{partition_count} partitions: at the ceiling, no dataset can be promoted; "
                "archive or merge pooled tenants first"
            ),
        )
    )

    return findings


def _grants_finding(role_privileges: dict[str, dict[str, bool]]) -> Finding:
    """The read path may read and must not write; the write path must do both."""

    if not role_privileges:
        return Finding(
            key="grants",
            title="read-only role cannot write",
            ok=True,
            fatal=False,
            detail=f"neither {READ_ROLE} nor {WRITE_ROLE} exists: the runtime connects as the "
            "owner, so there is no separate read path to check",
        )

    problems: list[str] = []
    read = role_privileges.get(READ_ROLE)
    if read is not None:
        if not read.get("select"):
            problems.append(f"{READ_ROLE} cannot SELECT (agent queries would be refused)")
        if read.get("insert"):
            problems.append(f"{READ_ROLE} can INSERT (a compiled query could write)")
    elif role_privileges:
        problems.append(f"{READ_ROLE} is missing")

    write = role_privileges.get(WRITE_ROLE)
    if write is not None and not (write.get("select") and write.get("insert")):
        problems.append(f"{WRITE_ROLE} cannot read and write")

    return Finding(
        key="grants",
        title="read-only role cannot write",
        ok=not problems,
        detail="; ".join(problems) or f"{READ_ROLE} reads only; {WRITE_ROLE} reads and writes",
    )


# --- backup manifest -------------------------------------------------------

_CREATE_TABLE = re.compile(
    r"^\s*CREATE TABLE (?:IF NOT EXISTS )?(?:ONLY )?(?:kb_data|\"kb_data\")\.(\w+)",
    re.IGNORECASE | re.MULTILINE,
)
_COPY_BLOCK = re.compile(
    r"^\s*COPY (?:kb_data|\"kb_data\")\.(\w+)\s",
    re.IGNORECASE | re.MULTILINE,
)


def objects_in_dump(dump: str) -> set[str]:
    """The tables a `pg_dump` output creates.

    A structure-only dump is still a backup of the schema, so this is the list the
    restore will recreate. Data blocks are counted separately, because the two
    failures are different: a missing table is loud, a missing COPY block restores
    a deployment that looks healthy and answers zero rows.
    """

    return set(_CREATE_TABLE.findall(dump))


def data_blocks_in_dump(dump: str) -> set[str]:
    """The tables a dump actually carries rows for."""

    return set(_COPY_BLOCK.findall(dump))


def compare_backup(
    *,
    dumped: set[str],
    live: set[str],
    dangling: dict[str, int],
    data_blocks: set[str] | None = None,
) -> list[Finding]:
    """Whether a dump would restore what this database holds.

    `dangling` is the live row count per table, and `data_blocks` the tables the
    dump carries rows for. Passing `data_blocks` as None compares structure only,
    which is what a `pg_dump -s` verification wants.
    """

    findings: list[Finding] = []

    missing = sorted(live - dumped)
    extra = sorted(dumped - live)
    findings.append(
        Finding(
            key="backup-tables",
            title="backup covers every table",
            ok=not missing and not extra,
            detail=(
                "every table is in the dump"
                if not missing and not extra
                else "; ".join(
                    part
                    for part in (
                        f"in the database but not the dump: {', '.join(missing)}"
                        if missing
                        else "",
                        f"in the dump but not the database: {', '.join(extra)}" if extra else "",
                    )
                    if part
                )
            ),
        )
    )

    if data_blocks is not None:
        without_rows = sorted(
            table for table, count in dangling.items() if count > 0 and table not in data_blocks
        )
        findings.append(
            Finding(
                key="backup-data",
                title="backup carries the rows it should",
                ok=not without_rows,
                detail=(
                    "every non-empty table has a data block"
                    if not without_rows
                    else f"empty in the dump but populated in the database: {', '.join(without_rows)}"
                ),
            )
        )

    return findings


# --- probe -----------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """Everything `evaluate` needs, read in one round trip count."""

    schema_exists: bool
    tables: set[str]
    partitions: set[str]
    rows_is_partitioned: bool
    role_privileges: dict[str, dict[str, bool]]
    partition_count: int
    row_counts: dict[str, int] = field(default_factory=dict)

    def findings(self) -> list[Finding]:
        return evaluate(
            tables=self.tables,
            partitions=self.partitions,
            rows_is_partitioned=self.rows_is_partitioned,
            role_privileges=self.role_privileges,
            partition_count=self.partition_count,
            schema_exists=self.schema_exists,
        )


_SCHEMA_EXISTS = "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema)"

_TABLES = """
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
"""

# `relispartition` is true for the indexes on a partition as well as the partition
# itself, so the relkind filter is load-bearing: without it a two-partition table
# reports ten and the ceiling check counts index objects.
_PARTITIONS = """
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relispartition AND c.relkind IN ('r', 'p')
"""

_ROWS_KIND = """
    SELECT c.relkind = 'p' AS is_partitioned
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relname = 'dataset_rows'
"""

# `has_table_privilege` raises for a role that does not exist, so the roles are
# filtered in the catalog first rather than wrapped in an exception handler.
_PRIVILEGES = """
    SELECT r.rolname,
           has_table_privilege(r.oid, format('%I.datasets', CAST(:schema AS text)), 'SELECT'),
           has_table_privilege(r.oid, format('%I.datasets', CAST(:schema AS text)), 'INSERT')
    FROM pg_roles r
    WHERE r.rolname IN (:read_role, :write_role)
"""

_PARTITION_COUNT = """
    SELECT count(*)
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relispartition AND c.relkind IN ('r', 'p')
"""

_ROW_COUNTS = """
    SELECT relname, n_live_tup
    FROM pg_stat_user_tables s
    JOIN pg_namespace n ON n.oid = s.schemaname::regnamespace
    WHERE n.nspname = :schema
"""


async def probe(db: Any) -> ProbeResult:
    """Read the deployment's shape.

    Every schema name is a bound parameter. The statements name `:schema` rather
    than splicing `kb_data` in, because the same functions are called with the
    restore target's schema when a drill runs.
    """

    params = {"schema": SCHEMA}

    schema_exists = bool((await db.execute(sql_text(_SCHEMA_EXISTS), params)).scalar_one())
    tables = {row[0] for row in (await db.execute(sql_text(_TABLES), params)).all()}
    partitions = {row[0] for row in (await db.execute(sql_text(_PARTITIONS), params)).all()}

    kind = (await db.execute(sql_text(_ROWS_KIND), params)).first()
    rows_is_partitioned = bool(kind and kind[0])

    privileges = {
        row[0]: {"select": bool(row[1]), "insert": bool(row[2])}
        for row in (
            await db.execute(
                sql_text(_PRIVILEGES),
                {**params, "read_role": READ_ROLE, "write_role": WRITE_ROLE},
            )
        ).all()
    }

    partition_count = int((await db.execute(sql_text(_PARTITION_COUNT), params)).scalar_one())

    return ProbeResult(
        schema_exists=schema_exists,
        tables=tables,
        partitions=partitions,
        rows_is_partitioned=rows_is_partitioned,
        role_privileges=privileges,
        partition_count=partition_count,
    )


# --- restore drill ---------------------------------------------------------

_LIVE_DATASETS = """
    SELECT id, storage_partition_key, current_version_no
    FROM kb_data.datasets
    WHERE archived_at IS NULL
    ORDER BY id
    LIMIT :limit
"""

_AS_OF_COUNT = """
    SELECT count(*)
    FROM kb_data.dataset_rows
    WHERE partition_key = :partition_key
      AND dataset_id = :dataset_id
      AND valid_from <= :version
      AND (valid_to IS NULL OR valid_to > :version)
"""


async def restore_drill(db: Any, *, sample: int = 5) -> list[Finding]:
    """Whether a restored database can answer the query the product promises.

    `check` proves the shape survived; this proves the *data* is reachable through
    it - the partitioned parent, the pooled partition the dataset routes to, and
    the as-of interval that selects a version. A restore that lost the partition
    swap passes `check` only if the parent is attached, and answers zero rows
    without complaint; this is the assertion that notices.

    A database with no datasets is not a failure: a fresh deployment restores to
    an empty schema, and refusing that would make the drill unusable on the day it
    matters most - before anything has been imported.
    """

    rows = (await db.execute(sql_text(_LIVE_DATASETS), {"limit": sample})).all()
    if not rows:
        return [
            Finding(
                key="restore-as-of",
                title="as-of query runs",
                ok=True,
                fatal=False,
                detail="no live datasets to query: the schema restored, the data did not exist",
            )
        ]

    findings: list[Finding] = []
    for dataset_id, partition_key, version in rows:
        count = (
            await db.execute(
                sql_text(_AS_OF_COUNT),
                {
                    "partition_key": partition_key,
                    "dataset_id": dataset_id,
                    "version": version,
                },
            )
        ).scalar_one()
        findings.append(
            Finding(
                key=f"restore-as-of:{dataset_id}",
                title=f"as-of query runs for dataset {dataset_id}",
                ok=True,
                detail=f"v{version} reads {int(count)} rows through partition {str(partition_key)[:8]}",
            )
        )
    return findings


async def row_counts(db: Any) -> dict[str, int]:
    """Live row estimates, for the backup check's data half.

    Estimates rather than `count(*)`: this runs against a production database,
    and a table showing zero rows after an ANALYZE is the only fact the check
    needs - whether it is 3 rows or 3 million changes nothing about whether the
    dump should carry a COPY block.
    """

    rows = (await db.execute(sql_text(_ROW_COUNTS), {"schema": SCHEMA})).all()
    return {row[0]: int(row[1] or 0) for row in rows}
