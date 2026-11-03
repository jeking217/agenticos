"""Operator commands for the Excel dataset schema.

Four questions, four commands. `check-kb-datasets` is the pre-flight: the
schema, its ten tables, both pool partitions, the read-only grant, and the
partition count - refusing with a non-zero exit so a release script can gate on
it. `verify-kb-datasets-backup` compares a dump against the live catalog, which is
the K12 control: an independent schema is exactly the thing that goes missing from
a backup without anybody noticing. `kb-datasets-partitions` lists what is attached
and how much each holds, which is what a promotion decision is made from.
`init-kb-datasets-roles` is the local half of the grant check: it creates the two
roles 0248 only grants *if they already exist*, so a developer machine can have
the same read-only path a deployment does (#T46).

They are separate commands rather than subcommands of one group because
`app.commands.command` wraps a function into a single `click.Command`; adding a
group there would be a second discovery mechanism for one feature.
"""

import asyncio
import json
from pathlib import Path

import click
from sqlalchemy import text as sql_text

from app.commands import command, error, info, success, warning
from app.db.session import get_db_context
from app.services.kb_dataset import inspect as kb_inspect


def _report(findings: list[kb_inspect.Finding], *, as_json: bool) -> int:
    if as_json:
        click.echo(
            json.dumps(
                {
                    "ok": kb_inspect.verdict(findings) == 0,
                    "findings": [
                        {
                            "key": finding.key,
                            "title": finding.title,
                            "ok": finding.ok,
                            "fatal": finding.fatal,
                            "detail": finding.detail,
                        }
                        for finding in findings
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return kb_inspect.verdict(findings)

    for finding in findings:
        line = f"{finding.title}: {finding.detail}"
        if finding.ok:
            # A warning that passed still has something to say - "the roles do not
            # exist" is why the grant check was skipped, and silence would read as
            # "the grants are right".
            if finding.fatal:
                success(line)
            else:
                warning(line)
        else:
            error(line)
    return kb_inspect.verdict(findings)


async def _check(as_json: bool) -> int:
    async with get_db_context() as db:
        result = await kb_inspect.probe(db)
    findings = result.findings()
    code = _report(findings, as_json=as_json)
    if code == 0:
        # Stated explicitly: a deployment where every assertion held and one where
        # the command silently found nothing are the same exit code otherwise.
        info(f"{kb_inspect.SCHEMA}: {len(kb_inspect.TABLES)} tables, {result.partition_count} partitions")
    return code


@command("check-kb-datasets", help="Check the Excel dataset schema is shaped as the code assumes")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output for a release gate")
def check_kb_datasets(as_json: bool) -> None:
    """Fail when a deployment could not serve dataset queries as designed.

    Run after `alembic upgrade head` and before pointing traffic at an instance.
    """

    raise SystemExit(asyncio.run(_check(as_json)))


_PGDMP_MAGIC = b"PGDMP"


def _read_dump(path: Path, *, from_custom: bool) -> str:
    """The dump as text, with an explanation instead of a decode error.

    A `-Fc` dump cannot be read here at all, so the message says what to do
    instead of letting a binary blob reach the regexes: `pg_restore -l` lists its
    objects, and `--format=plain` produces something this command can compare.
    """

    raw = path.read_bytes()
    if from_custom or raw.startswith(_PGDMP_MAGIC):
        raise click.UsageError(
            f"{path} is a custom-format archive, which is a binary container. "
            "Either dump with `pg_dump --format=plain --schema=kb_data`, or compare with "
            "`pg_restore --list` by hand."
        )
    return raw.decode("utf-8", errors="replace")


async def _verify(path: Path, *, structure_only: bool, as_json: bool) -> int:
    dump = _read_dump(path, from_custom=False)
    dumped = kb_inspect.objects_in_dump(dump)
    blocks = None if structure_only else kb_inspect.data_blocks_in_dump(dump)

    async with get_db_context() as db:
        result = await kb_inspect.probe(db)
        counts = {} if structure_only else await kb_inspect.row_counts(db)

    findings = kb_inspect.compare_backup(
        dumped=dumped,
        live=result.tables,
        dangling=counts,
        data_blocks=blocks,
    )
    code = _report(findings, as_json=as_json)
    if code == 0:
        success(f"{path.name}: {len(dumped)} tables match the live catalog")
    return code


@command(
    "verify-kb-datasets-backup",
    help="Compare a plain-format pg_dump against the live kb_data catalog",
)
@click.argument("dump", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--structure-only", is_flag=True, help="Skip the COPY-block comparison")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def verify_kb_datasets_backup(dump: Path, structure_only: bool, as_json: bool) -> None:
    """Catch a backup that has been missing the schema - or its rows - for months."""

    raise SystemExit(asyncio.run(_verify(dump, structure_only=structure_only, as_json=as_json)))


_PARTITION_ROWS = """
    SELECT c.relname,
           pg_get_expr(c.relpartbound, c.oid) AS bound,
           coalesce(s.n_live_tup, 0) AS rows
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
    WHERE n.nspname = :schema AND c.relispartition
    ORDER BY c.relname
"""


async def _partitions(as_json: bool) -> int:
    async with get_db_context() as db:
        rows = (await db.execute(sql_text(_PARTITION_ROWS), {"schema": kb_inspect.SCHEMA})).all()

    if as_json:
        click.echo(json.dumps([{"name": r[0], "bound": r[1], "rows": int(r[2])} for r in rows], indent=2))
        return 0

    if not rows:
        warning(f"no partitions in {kb_inspect.SCHEMA}: the rows table is not partitioned")
        return 1

    for name, bound, count in rows:
        click.echo(f"{name:<32} {int(count):>12,}  {bound}")
    pooled = sum(int(r[2]) for r in rows if r[0] in kb_inspect.POOL_PARTITIONS)
    total = sum(int(r[2]) for r in rows)
    if total:
        share = pooled * 100 // total
        info(f"{len(rows)} partitions, {total:,} rows, {share}% pooled")
        if share > 70:
            # The plan's K13: when the pools hold most of the data, promotion has
            # stopped happening and the sharding is decorative.
            warning(f"pooled share is {share}% (> 70%): check the promotion backlog")
    return 0


@command("kb-datasets-partitions", help="List attached partitions, their bounds and row counts")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def kb_datasets_partitions(as_json: bool) -> None:
    """What a promotion or an archive decision is made from."""

    raise SystemExit(asyncio.run(_partitions(as_json)))


# The two roles 0248 grants to *if they already exist*. A deployment creates them
# before migrating; a development machine usually has neither, which is why the
# grant check reports "no separate read path to check" there and passes - a
# warning that nobody acts on until somebody points the agent at `app_ro` and
# finds it does not exist.
#
# The grants below are deliberately the same statements 0248 runs rather than an
# import of them: a migration is a frozen snapshot of one moment, and a command
# that reads it would silently change what an applied migration did. If a later
# migration adds a table, its own grant block covers it and re-running this
# command re-applies this one.
_CREATE_ROLES = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_ro') THEN
        CREATE ROLE app_ro NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
        CREATE ROLE app_rw NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object THEN
    -- Two developers (or two CI jobs) racing on one cluster: the loser of the
    -- race wanted the role to exist, and it does.
    NULL;
END $$;
"""

_GRANT_ROLES = f"""
DO $$
BEGIN
    -- The database name is an identifier and Postgres will not bind one. `%I`
    -- quotes whatever `current_database()` returns, so a name with a dash or a
    -- quote in it is neither a syntax error nor an escape.
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_ro, app_rw', current_database());

    GRANT USAGE ON SCHEMA {kb_inspect.SCHEMA} TO app_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA {kb_inspect.SCHEMA} TO app_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA {kb_inspect.SCHEMA}
        GRANT SELECT ON TABLES TO app_ro;

    GRANT USAGE, CREATE ON SCHEMA {kb_inspect.SCHEMA} TO app_rw;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {kb_inspect.SCHEMA}
        TO app_rw;
    ALTER DEFAULT PRIVILEGES IN SCHEMA {kb_inspect.SCHEMA}
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
END $$;
"""

# `has_table_privilege` raises for a role that does not exist, so the roles are
# matched in the catalog first - the same order `inspect.probe` uses.
_APPLIED = """
    SELECT r.rolname, p.privilege
    FROM pg_roles r
    CROSS JOIN unnest(ARRAY['SELECT', 'INSERT']) AS p(privilege)
    WHERE r.rolname IN ('app_ro', 'app_rw')
      AND has_table_privilege(r.oid, :table, p.privilege)
"""


async def _init_roles() -> int:
    async with get_db_context() as db:
        await db.execute(sql_text(_CREATE_ROLES))
        await db.execute(sql_text(_GRANT_ROLES))
        await db.commit()

        granted = (
            await db.execute(
                sql_text(_APPLIED), {"table": f"{kb_inspect.SCHEMA}.datasets"}
            )
        ).all()

    app_ro = {row[1] for row in granted if row[0] == "app_ro"}
    app_rw = {row[1] for row in granted if row[0] == "app_rw"}
    info(f"app_ro: {', '.join(sorted(app_ro)) or 'no privileges'}")
    info(f"app_rw: {', '.join(sorted(app_rw)) or 'no privileges'}")

    # Asked of the catalogue rather than assumed from the statements above: a
    # GRANT on a schema with no tables succeeds and grants nothing, which is the
    # shape of "it worked yesterday" this check exists to catch.
    if not {"SELECT"} <= app_ro or "INSERT" in app_ro or not {"SELECT", "INSERT"} <= app_rw:
        error(
            f"the grants did not take: {kb_inspect.SCHEMA} has no tables to grant on yet "
            "(run `alembic upgrade head` first, then this again)"
        )
        return 1
    success(f"roles ready: app_ro reads {kb_inspect.SCHEMA}, app_rw reads and writes")
    return 0


@command(
    "init-kb-datasets-roles",
    help="Create app_ro / app_rw and grant them on the kb_data schema (local setup)",
)
def init_kb_datasets_roles() -> None:
    """The step a local database skips, and the one that makes the grant check real.

    Create the roles *before* `alembic upgrade head` if you want the migration's
    own grants too; running this afterwards grants the same privileges over the
    tables that already exist, which is what a machine that migrated first needs.
    """

    raise SystemExit(asyncio.run(_init_roles()))


async def _restore_drill(as_json: bool, sample: int) -> int:
    async with get_db_context() as db:
        result = await kb_inspect.probe(db)
        findings = result.findings()
        findings.extend(await kb_inspect.restore_drill(db, sample=sample))

    code = _report(findings, as_json=as_json)
    if code == 0:
        success("restored database answers a versioned read through the partitioned table")
    return code


@command(
    "verify-kb-datasets-restore",
    help="After restoring a dump into an empty database, prove it can answer a versioned read",
)
@click.option("--sample", default=5, show_default=True, help="How many datasets to read")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def verify_kb_datasets_restore(sample: int, as_json: bool) -> None:
    """The half `check` cannot see: the shape survived, but can it still answer?

    Run it against the restore target - point `POSTGRES_DB` at the empty database
    the dump was loaded into - before promoting that database to a real one.
    """

    raise SystemExit(asyncio.run(_restore_drill(as_json, sample)))
