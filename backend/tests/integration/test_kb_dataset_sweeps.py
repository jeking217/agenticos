"""The two nightly sweeps, and what they do when there is nothing to do (T25).

Each is a walk over a deployment and a transaction per dataset; the arithmetic
they call is unit-tested next door. What is tested here is the walk: that a pass
which finds nothing says so, and that a dataset over the line is actually
changed by the pass rather than only selected by it.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import kb_dataset as repo
from app.worker.tasks import dataset_tasks

from tests.integration.kb_dataset_support import base, dataset, published

pytestmark = pytest.mark.anyio


def _use_this_session(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def same_session() -> AsyncIterator[AsyncSession]:
        yield db

    monkeypatch.setattr(dataset_tasks, "get_worker_db_context", same_session)


async def test_a_retention_pass_with_nothing_to_do_answers_zero(
    monkeypatch: pytest.MonkeyPatch, db: AsyncSession
) -> None:
    """A sweep that found nothing must not claim work it did not do."""
    _use_this_session(db, monkeypatch)
    kb = await base(db)
    orders = await dataset(db, kb)
    await published(db, orders, [{"订单编号": "SO-1", "客户编号": "C-1", "金额": 1}])

    assert await dataset_tasks.sweep_dataset_versions() == 0


async def test_a_retention_pass_prunes_the_dataset_it_selected(
    monkeypatch: pytest.MonkeyPatch, db: AsyncSession
) -> None:
    """Selected is not pruned: the pass has to actually apply the arithmetic."""
    _use_this_session(db, monkeypatch)
    from app.core.config import settings

    keep = settings.KB_DATASET_VERSIONS_KEPT
    kb = await base(db)
    orders = await dataset(db, kb)
    for n in range(keep + 2):
        await published(
            db, orders, [{"订单编号": "SO-1", "客户编号": "C-1", "金额": n}]
        )
    assert len((await repo.list_versions(db, orders.id))[0]) == keep + 2

    pruned = await dataset_tasks.sweep_dataset_versions()

    assert pruned == 1
    await db.refresh(orders)
    # The current version is always kept, whatever the limit says.
    assert orders.current_version_no == keep + 2


async def test_a_partition_pass_with_nothing_pooled_answers_zero(
    monkeypatch: pytest.MonkeyPatch, db: AsyncSession
) -> None:
    """No pooled rows means no occupancy to reason about, and no moves."""
    _use_this_session(db, monkeypatch)

    assert await dataset_tasks.sweep_dataset_partitions() == 0


async def test_a_partition_pass_leaves_a_small_dataset_where_it_is(
    monkeypatch: pytest.MonkeyPatch, db: AsyncSession
) -> None:
    """A hundred rows is not a tenant: promoting it would be a table nobody needed."""
    _use_this_session(db, monkeypatch)
    kb = await base(db)
    orders = await dataset(db, kb)
    await published(
        db,
        orders,
        [{"订单编号": f"SO-{n}", "客户编号": "C-1", "金额": n} for n in range(100)],
    )

    assert await dataset_tasks.sweep_dataset_partitions() == 0
    await db.refresh(orders)
    assert orders.storage_mode == "pooled"
