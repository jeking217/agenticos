"""The read-only role, on a database that actually has one (T46).

`inspect.probe` asks whether `app_ro` may read and may not write, and passes with
a warning when the role does not exist at all - which is every development
machine, and the reason the check was decorative there. This file is the other
half: `init-kb-datasets-roles` creates the roles and grants them, and the test
below proves the promise Postgres actually makes rather than the one the script
intends.

Three claims:

1. `app_ro` reads `kb_data` and is *refused* a write - by the server, not by a
   convention in the application.
2. Once the roles exist, the release-gate check reports the grants as satisfied,
   so the two halves cannot disagree about what "granted" means.
3. Running the initialisation twice is not an error: a local setup gets re-run on
   every checkout, and a command that fails the second time is a command people
   stop running.

The test cluster is shared, so the roles are created idempotently and are left in
place - they are cluster objects, not rows, and dropping them would race with any
other suite that has just created them.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.kb_datasets import _init_roles
from app.services.kb_dataset import inspect as kb_inspect

pytestmark = pytest.mark.anyio


async def test_app_ro_reads_the_schema_and_is_refused_a_write(db: AsyncSession) -> None:
    """The privilege boundary, asked of Postgres rather than of the script.

    `SET ROLE` is how a session becomes `app_ro` without a second connection: the
    test account owns the tables, so it may impersonate the read-only role, and
    what the following statements are allowed to do is exactly what `app_ro` would
    be allowed to do.
    """
    assert await _init_roles() == 0

    await db.execute(text("SET ROLE app_ro"))
    read = await db.execute(text(f"SELECT count(*) FROM {kb_inspect.SCHEMA}.datasets"))
    assert int(read.scalar_one()) >= 0

    # A savepoint, so the refusal does not take the test's own transaction with
    # it - Postgres aborts everything after a failed statement in the same block.
    with pytest.raises(ProgrammingError) as refusal:
        async with db.begin_nested():
            await db.execute(
                text(
                    f"INSERT INTO {kb_inspect.SCHEMA}.datasets (id) VALUES (gen_random_uuid())"
                )
            )
    assert "permission denied" in str(refusal.value).lower()
    await db.execute(text("RESET ROLE"))


async def test_the_grant_check_agrees_once_the_roles_exist(db: AsyncSession) -> None:
    """The command and the release gate read the same catalog the same way.

    `inspect.probe` is run after the grants, so its answer is what a deploy would
    print: the grants finding has to be fatal-and-passing rather than the
    "no separate read path to check" warning it gives on a machine without roles.
    """
    assert await _init_roles() == 0

    result = await kb_inspect.probe(db)
    grants = next(
        finding for finding in result.findings() if finding.key == "grants"
    )
    assert grants.fatal, "the grants are a warning, not a check - the roles exist now"
    assert grants.ok, grants.detail
    assert kb_inspect.READ_ROLE in grants.detail
    assert kb_inspect.WRITE_ROLE in grants.detail


async def test_running_it_twice_is_not_an_error(db: AsyncSession) -> None:
    """A local setup re-runs this on every checkout, and a GRANT is idempotent."""
    assert await _init_roles() == 0
    assert await _init_roles() == 0
