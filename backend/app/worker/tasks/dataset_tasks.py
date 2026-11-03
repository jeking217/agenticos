"""The daily sweep that bounds how much history a dataset keeps.

Queued as `sweep-dataset-versions`, one tick a day. The arithmetic is in
`app.services.kb_dataset.retention`; what is here is the walk over a deployment
and the transaction boundary per dataset.

Per dataset, and committed one at a time. A sweep that held every dataset's
delete until the end would hold the pooled partitions' locks - and the
transaction-scoped audit chain's lock - for the whole run, which is a production
write stall the size of the deployment's history. One dataset that fails is
logged and the walk continues: the next pass retries it, and a pass that removed
nothing simply comes round again.
"""

import logging
from typing import Any

from app.core.config import settings
from app.db.session import get_worker_db_context
from app.worker import jobs

logger = logging.getLogger(__name__)

# Promotions per sweep. Each one is an `UPDATE` that rewrites every row of the
# dataset and a `CREATE TABLE` on the parent, so a sweep that promoted every
# candidate at once would be a write storm whose duration nobody bounded. The
# rest wait for tomorrow; a pool drains one tenant at a time anyway, because the
# share rule recomputes after each one leaves.
MAX_PROMOTIONS_PER_SWEEP = 5


async def run_import(job: jobs.ImportDataset) -> dict[str, Any]:
    """Publish the sheets a workbook import was accepted for.

    The row moves to `running` and commits before the first sheet, so a poller
    can tell "waiting for a worker" from "being worked on"; from there each sheet
    commits on its own (`imports.SavedProgress`), which is what makes both the
    progress bar and the recovery claim real rather than decorative.

    A sheet that fails is already recorded by `imports.commit` - the import ends
    `partial` and names it. What this adds is the case where the *process* ends:
    that is `settle_lost_import`, and the staged workbook is kept for it.
    """
    from app.repositories import kb_dataset as repo
    from app.services.kb_dataset import imports

    async with get_worker_db_context() as db:
        record = await repo.get_import(db, job.import_id)
        if record is None:
            logger.warning("kb_dataset_import: import %s is gone", job.import_id)
            return {"status": "missing"}
        if record.status not in {imports.QUEUED, imports.RUNNING, "partial", "failed"}:
            # Settled by an attempt that reached its last write and then died
            # before the job row caught up: publishing again could only mint a
            # version for a workbook that changed nothing.
            logger.info("kb_dataset_import: import %s is already %s", job.import_id, record.status)
            return {"status": record.status}

        record.status = imports.RUNNING
        await db.commit()

        try:
            result = await imports.commit(
                db,
                record=record,
                knowledge_base_id=job.knowledge_base_id,
                organization_id=job.organization_id,
                payload=imports.commit_spec(job),
                actor_user_id=job.actor_user_id,
                progress=imports.SavedProgress(db),
            )
        except Exception as exc:
            # Nothing was published (the refusals all happen before the first
            # sheet), so the row says why and stays retryable. Re-raised after
            # that: a job that failed is what the panel's error count is for.
            await db.rollback()
            record = await repo.get_import(db, job.import_id)
            if record is not None:
                await imports.give_up(db, record, str(exc)[:500])
            raise

        await imports.record_commit(
            db,
            record=record,
            knowledge_base_id=job.knowledge_base_id,
            organization_id=job.organization_id,
            actor_user_id=job.actor_user_id,
            result=result,
        )
        await db.commit()
        logger.info(
            "kb_dataset_import: import %s finished %s (%d sheet(s))",
            job.import_id,
            result.get("status"),
            len(job.sheets),
        )
        return result


async def settle_lost_import(job: jobs.ImportDataset, why: str) -> None:
    """Close an accepted import whose worker died, so it can be published again.

    Nothing else would: the staged workbook is still there and the row is still
    `running`, and the poller has no way to tell a slow import from an abandoned
    one. `failed` is the status `commit` accepts, so the retry is the button the
    person already has.
    """
    from app.repositories import kb_dataset as repo
    from app.services.kb_dataset import imports

    async with get_worker_db_context() as db:
        record = await repo.get_import(db, job.import_id)
        if record is None or record.status not in {imports.QUEUED, imports.RUNNING}:
            return
        await imports.give_up(db, record, why)
        logger.warning("kb_dataset_import: import %s abandoned (%s)", job.import_id, why)


async def sweep_dataset_versions() -> int:
    """Prune every dataset holding more versions than the deployment keeps.

    Returns how many datasets were pruned, which is what the panel's activity
    view reports as "work done" - a pass that found nothing to do answers 0
    rather than pretending otherwise.
    """
    from app.repositories import kb_dataset as repo
    from app.services.kb_dataset import retention

    keep = settings.KB_DATASET_VERSIONS_KEPT
    pruned = 0

    async with get_worker_db_context() as db:
        candidates = await repo.datasets_over_version_limit(
            db, keep=keep, limit=retention.MAX_PARTITIONS_PER_SWEEP
        )

    for dataset_id in candidates:
        try:
            async with get_worker_db_context() as db:
                dataset = await repo.get_dataset(db, dataset_id)
                if dataset is None:
                    continue
                result = await retention.apply(db, dataset, keep=keep)
        except Exception:
            logger.exception("kb_dataset_retention: dataset %s failed", dataset_id)
            continue
        if result.changed:
            pruned += 1

    if pruned:
        logger.info("kb_dataset_retention: pruned %d dataset(s)", pruned)
    return pruned


async def sweep_dataset_partitions() -> int:
    """Move the datasets that have outgrown a shared pool onto their own relation.

    Queued as `sweep-dataset-partitions`, once a day, before the retention sweep.
    The decision is `partitions.candidates`, which is pure and unit-tested; what
    is here is reading the occupancy it needs and running the move one dataset
    per transaction.

    Per dataset, because a promotion holds locks on the parent and rewrites every
    row of the dataset: one transaction for the whole sweep would be a stall the
    size of the deployment's largest tenant. One failure is logged and the walk
    continues - the pool is no worse off than before the run.

    Returns how many datasets were promoted, which is what the activity view
    reports. A pass that found nothing answers 0 rather than pretending it did
    something.
    """
    from app.db.models.kb_dataset import pool_for
    from app.repositories import kb_dataset as repo
    from app.services.kb_dataset import partitions

    async with get_worker_db_context() as db:
        counts = await repo.pooled_row_counts(db)

    if not counts:
        return 0

    # Occupancy per pool, because the share rule is about the pool and not the
    # deployment: one dataset holding 80% of POOL_A is the reason to promote it,
    # and says nothing about POOL_B.
    occupancy: dict[object, int] = {}
    for dataset_id, rows in counts.items():
        occupancy[pool_for(dataset_id)] = occupancy.get(pool_for(dataset_id), 0) + rows

    candidates: list[partitions.Candidate] = []
    for pool, pool_rows in occupancy.items():
        on_pool = {dataset: rows for dataset, rows in counts.items() if pool_for(dataset) == pool}
        candidates.extend(partitions.candidates(on_pool, pool_rows=pool_rows))

    # Largest first across both pools: with a bounded number of moves per run, the
    # one that is actually distorting a pool should be the one that goes.
    candidates.sort(key=lambda candidate: candidate.rows, reverse=True)

    promoted = 0
    for candidate in candidates[:MAX_PROMOTIONS_PER_SWEEP]:
        try:
            async with get_worker_db_context() as db:
                dataset = await repo.get_dataset_for_update(db, candidate.dataset_id)
                if dataset is None or dataset.storage_mode != "pooled":
                    continue
                moved = await partitions.promote_dataset(db, dataset)
        except Exception:
            logger.exception("kb_dataset_partitions: dataset %s failed", candidate.dataset_id)
            continue
        promoted += 1
        logger.info(
            "kb_dataset_partitions: promoted %s (%s, %d rows moved)",
            candidate.dataset_id,
            candidate.reason,
            moved,
        )

    return promoted
