"""Keeping a dataset's history bounded without making an old answer wrong.

A version is append-only, so nothing prunes itself: every import adds rows and
closes the ones it replaced, and a sheet refreshed weekly for two years leaves a
hundred versions and every row any of them ever held. This module is the rule
that bounds it, and the rule is one sentence: **a version is kept while somebody
could still be reading it**.

Three things count as still being read, and each is a refusal to delete rather
than a preference:

* the **current version**, always - it is what every new question is answered
  from;
* the last `keep` versions, the window a person browses in the console;
* any version **pinned** by `dataset_version_pins`, which is how an answer that
  quoted version 7 keeps version 7 - the promise "your old answer still says what
  it said" is exactly this table, and a sweep that ignored it would break the one
  guarantee the multi-version design exists for.

Rows go with the versions that were their last reader. A row closed at version 12
is invisible to every version at or after 12, so once the lowest surviving
version is 13 the row is unreachable and can go. A row still open (`valid_to IS
NULL`) is never touched, whatever the cutoff: it is the current answer.

What this deliberately does *not* do: rewrite history. A pruned version is gone,
not merged into its neighbour, and `compare` across a pruned boundary reports the
version as absent rather than inventing a diff. That is visible on purpose - the
console lists which versions a dataset still holds, so "v3 is not retained" reads
as an answer, where a silent merge would read as a change of content.
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.models.kb_dataset import Dataset

logger = logging.getLogger(__name__)

MINIMUM_KEPT = 2
"""The fewest versions a sweep may be asked to keep.

One would prune the version a dataset was published from, leaving the current
version with nothing to compare against and a console whose Versions tab answers
"no history" about a dataset that has been refreshed twenty times. Two is the
smallest window in which the feature still shows what it is for."""

ROW_BATCH = 2_000
"""Rows removed per statement. A dataset refreshed weekly since it was created has
years of closed rows to remove, and one `DELETE` over all of them holds a lock on
a pooled partition shared with every other dataset for the length of it."""

MAX_PARTITIONS_PER_SWEEP = 200
"""Datasets visited per sweep. The rest wait for the next pass, which is what a
daily sweep is for; the alternative is one run whose duration nobody bounded."""


@dataclass(frozen=True)
class RetentionPlan:
    """Which versions a sweep would remove, and why the others stay."""

    keep: int
    current_version_no: int
    retained: list[int]
    pinned: list[int]
    purge: list[int]
    """Oldest first. Empty means there is nothing to do."""

    @property
    def row_cutoff(self) -> int | None:
        """The version rows are judged against: the lowest one that survives.

        Not the highest one removed, and the difference is load-bearing. A row
        closed at version N is invisible from N onwards, so it may go once no
        surviving version is below N - which is `min(retained)`. Where the purge
        set is a prefix - the ordinary case - the two agree up to one version; a
        version kept *inside* the window's range (a pinned one, or a current
        version older than its history) makes them differ, and the highest-removed
        reading would then delete a row the surviving older version still shows.
        """
        return min(self.retained) if self.purge and self.retained else None


@dataclass
class RetentionResult:
    dataset_id: UUID
    versions_removed: list[int] = field(default_factory=list)
    rows_removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.versions_removed)


def plan(
    *,
    current_version_no: int,
    versions: list[int],
    pinned: set[int],
    keep: int,
) -> RetentionPlan:
    """Decide what survives, from the version numbers a dataset holds.

    Pure arithmetic over the numbers, so the rule can be read - and tested -
    without a database. `versions` is every version the dataset has, in any
    order; the plan comes back ordered.
    """
    keep = max(keep, MINIMUM_KEPT)
    ordered = sorted(versions)
    window = set(ordered[-keep:]) if keep else set()
    protected = window | pinned | {current_version_no}
    retained = [version for version in ordered if version in protected]
    purge = [version for version in ordered if version not in protected]
    return RetentionPlan(
        keep=keep,
        current_version_no=current_version_no,
        retained=retained,
        pinned=sorted(pinned & set(ordered)),
        purge=purge,
    )


async def version_numbers(db: AsyncSession, dataset_id: UUID) -> list[int]:
    rows = await db.execute(
        text("SELECT version_no FROM kb_data.dataset_versions WHERE dataset_id = :ds"),
        {"ds": dataset_id},
    )
    return [int(row[0]) for row in rows]


async def remove_rows_through(db: AsyncSession, dataset: Dataset, *, through_version: int) -> int:
    """Delete the rows no surviving version can see, a batch at a time.

    `valid_to <= through_version` is the whole condition: a row closed at or
    before the cutoff is invisible to the cutoff version and every later one. The
    tombstones go with them - a deleted record's marker exists to keep it deleted
    for the versions that could still show it.
    """
    removed = 0
    while True:
        result = await db.execute(
            text(
                """
                DELETE FROM kb_data.dataset_rows
                WHERE id IN (
                    SELECT id FROM kb_data.dataset_rows
                    WHERE partition_key = :pk
                      AND dataset_id = :ds
                      AND valid_to IS NOT NULL
                      AND valid_to <= :through
                    LIMIT :batch
                )
                """
            ),
            {
                "pk": dataset.storage_partition_key,
                "ds": dataset.id,
                "through": through_version,
                "batch": ROW_BATCH,
            },
        )
        batch = int(result.rowcount or 0)
        removed += batch
        if batch < ROW_BATCH:
            return removed


async def remove_versions(db: AsyncSession, dataset_id: UUID, versions: list[int]) -> int:
    if not versions:
        return 0
    result = await db.execute(
        text(
            "DELETE FROM kb_data.dataset_versions "
            "WHERE dataset_id = :ds AND version_no = ANY(:versions)"
        ),
        {"ds": dataset_id, "versions": versions},
    )
    return int(result.rowcount or 0)


async def apply(db: AsyncSession, dataset: Dataset, *, keep: int) -> RetentionResult:
    """Prune one dataset in `db`'s transaction. The caller commits.

    Rows first, versions second, and in that order because a failure between the
    two must leave versions naming rows that still exist - a version whose rows
    are gone renders as an empty page, which reads as "this dataset was empty at
    v3" rather than as a failed sweep.
    """
    current = await version_numbers(db, dataset.id)
    pinned_rows = await db.execute(
        text("SELECT DISTINCT version_no FROM kb_data.dataset_version_pins WHERE dataset_id = :ds"),
        {"ds": dataset.id},
    )
    pinned = {int(row[0]) for row in pinned_rows}
    decision = plan(
        current_version_no=dataset.current_version_no,
        versions=current,
        pinned=pinned,
        keep=keep,
    )
    result = RetentionResult(dataset_id=dataset.id, versions_removed=list(decision.purge))
    if not decision.purge:
        return result
    cutoff = decision.row_cutoff
    assert cutoff is not None
    result.rows_removed = await remove_rows_through(db, dataset, through_version=cutoff)
    await remove_versions(db, dataset.id, decision.purge)
    logger.info(
        "kb_dataset_retention: dataset=%s removed versions %s (through v%d), %d row(s)",
        dataset.id,
        decision.purge,
        cutoff,
        result.rows_removed,
    )
    return result
