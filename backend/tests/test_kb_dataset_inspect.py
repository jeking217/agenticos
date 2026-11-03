"""The two questions an operator asks about a dataset deployment.

`check` answers "is this deployment wired the way the schema says it is": the
schema exists, ten tables are there, both pool partitions are attached, the
read-only role cannot write, and the partition count has not run into the
planner's ceiling. `verify-backup` answers the K12 question instead - whether a
dump actually contains what the live database holds - which is the failure mode
that only shows up on the day somebody restores.

Both are pure decisions over what a probe returned, so most of this file needs
no database at all: it is the decision table that gets tested, and the probe is
tested separately against a session that answers with rows.
"""

import uuid
from typing import Any

import pytest

from app.services.kb_dataset import inspect as kb_inspect

pytestmark = pytest.mark.anyio


def test_a_healthy_deployment_reports_nothing_fatal() -> None:
    findings = kb_inspect.evaluate(
        tables=set(kb_inspect.TABLES),
        partitions={"dataset_rows_pool_a", "dataset_rows_pool_b"},
        rows_is_partitioned=True,
        role_privileges={"app_ro": {"select": True, "insert": False}, "app_rw": {"select": True, "insert": True}},
        partition_count=2,
    )

    assert kb_inspect.verdict(findings) == 0
    assert all(finding.ok for finding in findings)


def test_a_missing_table_names_the_table() -> None:
    tables = set(kb_inspect.TABLES) - {"dataset_versions"}

    findings = kb_inspect.evaluate(
        tables=tables,
        partitions={"dataset_rows_pool_a", "dataset_rows_pool_b"},
        rows_is_partitioned=True,
        role_privileges={"app_ro": {"select": True, "insert": False}, "app_rw": {"select": True, "insert": True}},
        partition_count=2,
    )

    assert kb_inspect.verdict(findings) == 1
    table_finding = next(f for f in findings if f.key == "tables")
    assert not table_finding.ok
    assert "dataset_versions" in table_finding.detail


def test_both_pool_partitions_have_to_be_attached() -> None:
    findings = kb_inspect.evaluate(
        tables=set(kb_inspect.TABLES),
        partitions={"dataset_rows_pool_a"},
        rows_is_partitioned=True,
        role_privileges={"app_ro": {"select": True, "insert": False}, "app_rw": {"select": True, "insert": True}},
        partition_count=1,
    )

    partition_finding = next(f for f in findings if f.key == "partitions")
    assert not partition_finding.ok
    assert "pool_b" in partition_finding.detail


def test_rows_that_are_not_a_partitioned_table_fail() -> None:
    """A plain `dataset_rows` means the migration ran without its DDL half.

    The table would still accept rows, so every functional test would pass while
    the pool partitions sat unattached and the first promotion had nothing to
    move.
    """

    findings = kb_inspect.evaluate(
        tables=set(kb_inspect.TABLES),
        partitions={"dataset_rows_pool_a", "dataset_rows_pool_b"},
        rows_is_partitioned=False,
        role_privileges={"app_ro": {"select": True, "insert": False}, "app_rw": {"select": True, "insert": True}},
        partition_count=2,
    )

    assert not next(f for f in findings if f.key == "partitions").ok


def test_a_read_only_role_that_can_write_fails_the_check() -> None:
    findings = kb_inspect.evaluate(
        tables=set(kb_inspect.TABLES),
        partitions={"dataset_rows_pool_a", "dataset_rows_pool_b"},
        rows_is_partitioned=True,
        role_privileges={"app_ro": {"select": True, "insert": True}, "app_rw": {"select": True, "insert": True}},
        partition_count=2,
    )

    grants = next(f for f in findings if f.key == "grants")
    assert not grants.ok
    assert "app_ro" in grants.detail


def test_a_read_only_role_that_cannot_read_fails_too() -> None:
    """Missing SELECT is the other half: an agent's queries would be refused.

    Checked because the grant block is conditional on the roles existing, and a
    deployment that created `app_ro` after the migration would silently have no
    grants at all.
    """

    findings = kb_inspect.evaluate(
        tables=set(kb_inspect.TABLES),
        partitions={"dataset_rows_pool_a", "dataset_rows_pool_b"},
        rows_is_partitioned=True,
        role_privileges={"app_ro": {"select": False, "insert": False}, "app_rw": {"select": True, "insert": True}},
        partition_count=2,
    )

    assert not next(f for f in findings if f.key == "grants").ok


def test_absent_roles_are_a_warning_not_a_failure() -> None:
    """A deployment that never defined these roles has no query path to protect.

    The runtime connects as the owner in that case, which is how every local
    setup runs. Refusing to start there would make the check unusable on exactly
    the machines somebody first tries it on.
    """

    findings = kb_inspect.evaluate(
        tables=set(kb_inspect.TABLES),
        partitions={"dataset_rows_pool_a", "dataset_rows_pool_b"},
        rows_is_partitioned=True,
        role_privileges={},
        partition_count=2,
    )

    grants = next(f for f in findings if f.key == "grants")
    assert grants.ok
    assert not grants.fatal
    assert "app_ro" in grants.detail
    assert kb_inspect.verdict(findings) == 0


def test_partition_count_at_the_ceiling_fails() -> None:
    findings = kb_inspect.evaluate(
        tables=set(kb_inspect.TABLES),
        partitions={"dataset_rows_pool_a", "dataset_rows_pool_b"},
        rows_is_partitioned=True,
        role_privileges={"app_ro": {"select": True, "insert": False}, "app_rw": {"select": True, "insert": True}},
        partition_count=kb_inspect.PARTITION_LIMIT,
    )

    assert not next(f for f in findings if f.key == "partition-count").ok


def test_partition_count_just_below_the_ceiling_passes() -> None:
    findings = kb_inspect.evaluate(
        tables=set(kb_inspect.TABLES),
        partitions={"dataset_rows_pool_a", "dataset_rows_pool_b"},
        rows_is_partitioned=True,
        role_privileges={"app_ro": {"select": True, "insert": False}, "app_rw": {"select": True, "insert": True}},
        partition_count=kb_inspect.PARTITION_LIMIT - 1,
    )

    assert next(f for f in findings if f.key == "partition-count").ok


def test_the_schema_itself_being_absent_is_the_first_finding() -> None:
    findings = kb_inspect.evaluate(
        tables=set(),
        partitions=set(),
        rows_is_partitioned=False,
        role_privileges={},
        partition_count=0,
        schema_exists=False,
    )

    assert findings[0].key == "schema"
    assert not findings[0].ok
    assert kb_inspect.verdict(findings) == 1


# --- backup manifest -------------------------------------------------------


DUMP = """
CREATE TABLE kb_data.datasets (
    id uuid NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_data.dataset_rows_pool_a (
    id uuid NOT NULL
);
CREATE INDEX dataset_rows_history_idx ON kb_data.dataset_rows USING btree (dataset_id);
ALTER TABLE ONLY kb_data.dataset_rows ATTACH PARTITION kb_data.dataset_rows_pool_a FOR VALUES IN ('00000000-0000-4000-8000-00000000a001');
COPY kb_data.dataset_versions (id) FROM stdin;
"""


def test_a_dump_is_read_for_the_tables_it_creates() -> None:
    objects = kb_inspect.objects_in_dump(DUMP)

    assert objects == {"datasets", "dataset_rows_pool_a"}


def test_a_dump_is_read_for_the_tables_it_copies() -> None:
    """`pg_dump -a` emits COPY without CREATE, and that is still a backup."""

    objects = kb_inspect.data_blocks_in_dump("COPY kb_data.dataset_rows_pool_b (id) FROM stdin;\n")

    assert objects == {"dataset_rows_pool_b"}


def test_a_complete_backup_passes() -> None:
    findings = kb_inspect.compare_backup(
        dumped={"datasets", "dataset_versions"},
        live={"datasets", "dataset_versions"},
        dangling={"dataset_versions": 3},
    )

    assert kb_inspect.verdict(findings) == 0


def test_a_backup_missing_a_table_is_refused_by_name() -> None:
    findings = kb_inspect.compare_backup(
        dumped={"datasets"},
        live={"datasets", "dataset_versions"},
        dangling={},
    )

    assert kb_inspect.verdict(findings) == 1
    assert "dataset_versions" in findings[0].detail


def test_a_backup_carrying_a_table_the_database_does_not_have_is_refused() -> None:
    """The other direction: a dump from a newer deployment restored into an older one."""

    findings = kb_inspect.compare_backup(
        dumped={"datasets", "dataset_operations", "dataset_metrics"},
        live={"datasets", "dataset_operations"},
        dangling={},
    )

    assert kb_inspect.verdict(findings) == 1
    assert "dataset_metrics" in findings[0].detail


def test_a_backup_that_dropped_rows_is_refused() -> None:
    """`pg_dump --exclude-table-data`, or a dump taken mid-restore.

    A structure-only dump restores an empty deployment that looks healthy: `check`
    passes, the console lists datasets, and every query answers zero. Counting the
    COPY blocks is what separates that from a real backup.
    """

    findings = kb_inspect.compare_backup(
        dumped={"datasets", "dataset_versions"},
        live={"datasets", "dataset_versions"},
        dangling={"dataset_versions": 3},
        data_blocks={"datasets"},
    )

    assert kb_inspect.verdict(findings) == 1
    data_finding = next(f for f in findings if f.key == "backup-data")
    assert not data_finding.ok
    assert "dataset_versions" in data_finding.detail


# --- probe -----------------------------------------------------------------


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._rows[0][0]


class _ProbeSession:
    """Answers each probe statement in the order `probe` asks them."""

    def __init__(self, answers: list[list[Any]]) -> None:
        self.answers = answers
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        self.statements.append(str(statement))
        self.params.append(params or {})
        return _Rows(self.answers.pop(0))


def _sql(session: _ProbeSession) -> str:
    return " ".join(session.statements)


async def test_probe_reads_the_schema_the_tables_the_partitions_and_the_grants() -> None:
    session = _ProbeSession(
        [
            [(True,)],  # schema exists
            [("datasets",), ("dataset_versions",)],  # tables
            [("dataset_rows_pool_a",), ("dataset_rows_pool_b",)],  # partitions
            [(True,)],  # dataset_rows is a partitioned table
            [("app_ro", True, False), ("app_rw", True, True)],  # privileges
            [(2,)],  # partition count
        ]
    )

    result = await kb_inspect.probe(session)  # type: ignore[arg-type]

    assert result.schema_exists is True
    assert result.tables == {"datasets", "dataset_versions"}
    assert result.partitions == {"dataset_rows_pool_a", "dataset_rows_pool_b"}
    assert result.rows_is_partitioned is True
    assert result.role_privileges["app_ro"] == {"select": True, "insert": False}
    sql = _sql(session)
    # Every statement names the schema as a bound parameter, never spliced in.
    assert "kb_data" not in sql.replace(":schema", "")
    assert all(":schema" in statement for statement in session.statements[:3])


async def test_probe_asks_for_the_partition_count_from_the_catalog() -> None:
    session = _ProbeSession(
        [
            [(True,)],
            [],
            [],
            [(False,)],
            [],
            [(7,)],
        ]
    )

    result = await kb_inspect.probe(session)  # type: ignore[arg-type]

    assert result.partition_count == 7
    assert "relispartition" in _sql(session)


async def test_probe_reads_privileges_only_for_roles_that_exist() -> None:
    """`has_table_privilege` raises for an unknown role, so the probe filters first."""

    session = _ProbeSession(
        [
            [(True,)],
            [],
            [],
            [("p",)],
            [("app_ro", True, False)],
            [(2,)],
        ]
    )

    result = await kb_inspect.probe(session)  # type: ignore[arg-type]

    assert "app_rw" not in result.role_privileges
    privileges_sql = session.statements[4]
    assert "pg_roles" in privileges_sql


# --- restore drill ---------------------------------------------------------


async def test_a_restored_database_with_no_datasets_is_not_a_failure() -> None:
    """A fresh deployment restores to an empty schema; the drill still has to pass."""

    session = _ProbeSession([[]])

    findings = await kb_inspect.restore_drill(session)  # type: ignore[arg-type]

    assert kb_inspect.verdict(findings) == 0
    assert findings[0].fatal is False
    assert "no live datasets" in findings[0].detail


async def test_every_live_dataset_is_read_through_its_own_partition() -> None:
    dataset_id = uuid.uuid4()
    partition_key = uuid.uuid4()
    session = _ProbeSession([[(dataset_id, partition_key, 7)], [(42,)]])

    findings = await kb_inspect.restore_drill(session)  # type: ignore[arg-type]

    assert kb_inspect.verdict(findings) == 0
    assert "42 rows" in findings[0].detail
    # The partition key is a bound parameter, so a dataset on a dedicated partition
    # is read through that partition rather than the pool the checker assumed.
    params = session.params[1]
    assert params["partition_key"] == partition_key
    assert params["dataset_id"] == dataset_id
    assert params["version"] == 7
    assert ":partition_key" in session.statements[1]


async def test_the_drill_samples_rather_than_walking_every_dataset() -> None:
    session = _ProbeSession([[]])

    await kb_inspect.restore_drill(session, sample=3)  # type: ignore[arg-type]

    assert session.params[0]["limit"] == 3
