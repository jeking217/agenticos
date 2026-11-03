"""The operator commands, and the exit codes a release script gates on (T48).

`inspect.py` decides whether a deployment is shaped as the code assumes; these
tests are about the answer reaching a shell. That is a separate claim: a check
that prints a failure and exits zero is a check a deploy pipeline steps over, and
the three commands here are exactly what `scripts/kb_datasets_golive.sh` runs.

The database is not involved. `probe` and `restore_drill` are patched with the
catalog rows they would have returned, so what is under test is the wiring - the
finding rendered, the exit code, the `--json` shape - while the decisions
themselves are covered by `tests/test_kb_dataset_inspect.py`.

Sync tests rather than `anyio`: each command builds its own event loop with
`asyncio.run`, which a running loop would refuse.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.commands import kb_datasets as cmd
from app.services.kb_dataset import inspect as kb_inspect


@asynccontextmanager
async def _session(db: Any = None) -> AsyncIterator[Any]:
    yield db


def _healthy() -> kb_inspect.ProbeResult:
    return kb_inspect.ProbeResult(
        schema_exists=True,
        tables=set(kb_inspect.TABLES) | set(kb_inspect.POOL_PARTITIONS),
        partitions=set(kb_inspect.POOL_PARTITIONS),
        rows_is_partitioned=True,
        role_privileges={
            "app_ro": {"select": True, "insert": False},
            "app_rw": {"select": True, "insert": True},
        },
        partition_count=len(kb_inspect.POOL_PARTITIONS),
    )


def _shape_built_from_models() -> kb_inspect.ProbeResult:
    """What a schema created by `Base.metadata.create_all` looks like.

    Every table is there and `dataset_rows` is an ordinary table, because only the
    migration's raw DDL makes it partitioned. This is the shape of the test suite's
    own database, and the reason a deploy that never ran 0248's second half must be
    refused rather than merely reported.
    """
    return kb_inspect.ProbeResult(
        schema_exists=True,
        tables=set(kb_inspect.TABLES),
        partitions=set(),
        rows_is_partitioned=False,
        role_privileges={},
        partition_count=0,
    )


def _check(result: kb_inspect.ProbeResult, *arguments: str) -> Any:
    with (
        patch.object(cmd, "get_db_context", lambda: _session()),
        patch.object(kb_inspect, "probe", _async(result)),
    ):
        return CliRunner().invoke(cmd.check_kb_datasets, list(arguments))


def _async(value: Any) -> Any:
    async def answer(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return answer


def test_a_healthy_deployment_exits_zero() -> None:
    result = _check(_healthy())

    assert result.exit_code == 0, result.output
    assert "read-only role cannot write" in result.output
    assert "2 partitions" in result.output


def test_a_schema_without_partitions_is_refused() -> None:
    """The failure that answers zero rows instead of failing: exit non-zero, and say
    which half of the migration did not run."""
    result = _check(_shape_built_from_models())

    assert result.exit_code == 1
    assert "dataset_rows is not a partitioned table" in result.output
    assert "pool partitions attached" in result.output


def test_json_output_carries_the_verdict_and_every_finding() -> None:
    result = _check(_shape_built_from_models(), "--json")

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    keys = {finding["key"] for finding in payload["findings"]}
    assert {"schema", "tables", "partitions", "grants", "partition-count"} <= keys
    assert all("detail" in finding for finding in payload["findings"])


def test_a_dump_missing_a_table_is_refused(tmp_path: Path) -> None:
    dump = tmp_path / "kb_data.sql"
    dump.write_text(
        "CREATE TABLE kb_data.datasets (\n    id uuid\n);\n"
        "COPY kb_data.datasets (id) FROM stdin;\n",
        encoding="utf-8",
    )

    with (
        patch.object(cmd, "get_db_context", lambda: _session()),
        patch.object(kb_inspect, "probe", _async(_healthy())),
        patch.object(kb_inspect, "row_counts", _async({"datasets": 3})),
    ):
        result = CliRunner().invoke(cmd.verify_kb_datasets_backup, [str(dump)])

    assert result.exit_code == 1
    assert "not the dump" in result.output
    assert "dataset_rows" in result.output


def test_a_custom_format_archive_is_explained_not_decoded(tmp_path: Path) -> None:
    """A `-Fc` dump is binary; the command says what to do instead of failing
    somewhere inside a regex with a decode error."""
    dump = tmp_path / "kb_data.dump"
    dump.write_bytes(b"PGDMP\x01\x0f\x00")

    with patch.object(cmd, "get_db_context", lambda: _session()):
        result = CliRunner().invoke(cmd.verify_kb_datasets_backup, [str(dump)])

    assert result.exit_code != 0
    assert "custom-format archive" in result.output


def test_the_restore_drill_reports_what_it_read() -> None:
    finding = kb_inspect.Finding(
        key="restore-as-of:abc",
        title="as-of query runs for dataset abc",
        ok=True,
        detail="v3 reads 40 rows through partition 0000000a",
    )

    with (
        patch.object(cmd, "get_db_context", lambda: _session()),
        patch.object(kb_inspect, "probe", _async(_healthy())),
        patch.object(kb_inspect, "restore_drill", _async([finding])),
    ):
        result = CliRunner().invoke(cmd.verify_kb_datasets_restore, [])

    assert result.exit_code == 0, result.output
    assert "v3 reads 40 rows" in result.output


def test_an_empty_restore_is_a_warning_not_a_failure() -> None:
    """A fresh deployment restores to nothing; refusing it would make the drill
    unusable on the day it matters most."""
    finding = kb_inspect.Finding(
        key="restore-as-of",
        title="as-of query runs",
        ok=True,
        fatal=False,
        detail="no live datasets to query: the schema restored, the data did not exist",
    )

    with (
        patch.object(cmd, "get_db_context", lambda: _session()),
        patch.object(kb_inspect, "probe", _async(_healthy())),
        patch.object(kb_inspect, "restore_drill", _async([finding])),
    ):
        result = CliRunner().invoke(cmd.verify_kb_datasets_restore, [])

    assert result.exit_code == 0, result.output
    assert "no live datasets" in result.output


@pytest.mark.parametrize("command", ["check-kb-datasets", "verify-kb-datasets-restore"])
def test_the_commands_are_registered(command: str) -> None:
    """Registered under the name a script types, discovered from the package."""
    from app.commands import discover_commands

    names = {registered.name for registered in discover_commands()}
    assert command in names
