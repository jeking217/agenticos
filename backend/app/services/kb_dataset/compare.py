"""Comparing two versions, in the terms a person uses.

Four outcomes, not two. "Gone from this version" is not one thing:

- **removed** - the record was deleted through maintenance, so there is a
  tombstone explaining it,
- **missing** - the record simply is not in this version at all, which after the
  "absence is not deletion" rule should only happen through retention or a
  restore to an earlier point.

Calling both "deleted" would put a word in the data's mouth.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.db.models.kb_dataset import Dataset
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import DatasetCompareResult, RowChange
from app.services.kb_dataset.versions import canonical


def _live(rows: Any) -> dict[str, dict[str, Any]]:
    return {row.record_key: dict(row.values) for row in rows if not row.is_deleted}


def _tombstones(rows: Any) -> set[str]:
    return {row.record_key for row in rows if row.is_deleted}


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    names = set(before) | set(after)
    return sorted(
        name
        for name in names
        if canonical({name: before.get(name)}) != canonical({name: after.get(name)})
    )


async def compare(
    db: AsyncSession,
    dataset: Dataset,
    *,
    from_version: int,
    to_version: int,
    limit: int = 200,
) -> DatasetCompareResult:
    if from_version == to_version:
        return DatasetCompareResult(from_version=from_version, to_version=to_version)
    if from_version > dataset.current_version_no or to_version > dataset.current_version_no:
        raise BadRequestError(message="Both versions must have been published")

    before_rows = await repo.rows_as_of(
        db,
        partition_key=dataset.storage_partition_key,
        dataset_id=dataset.id,
        version_no=from_version,
    )
    after_rows = await repo.rows_as_of(
        db,
        partition_key=dataset.storage_partition_key,
        dataset_id=dataset.id,
        version_no=to_version,
    )

    before = _live(before_rows)
    after = _live(after_rows)
    tombstones = _tombstones(after_rows)

    result = DatasetCompareResult(from_version=from_version, to_version=to_version)
    changes: list[RowChange] = []

    for key, values in after.items():
        if key not in before:
            result.added += 1
            changes.append(RowChange(record_key=key, change="added", after=values))
        elif canonical(before[key]) != canonical(values):
            result.changed += 1
            changes.append(
                RowChange(
                    record_key=key,
                    change="changed",
                    before=before[key],
                    after=values,
                    changed_fields=_changed_fields(before[key], values),
                )
            )
        else:
            result.unchanged += 1

    for key, values in before.items():
        if key in after:
            continue
        if key in tombstones:
            result.removed += 1
            changes.append(RowChange(record_key=key, change="removed", before=values))
        else:
            result.missing += 1
            changes.append(RowChange(record_key=key, change="missing", before=values))

    # Additions and changes first: they are what somebody comparing versions is
    # usually looking for, and a truncated list should not be only removals.
    order = {"changed": 0, "added": 1, "removed": 2, "missing": 3}
    changes.sort(key=lambda change: (order[change.change], change.record_key))
    result.truncated = len(changes) > limit
    result.rows = changes[:limit]
    return result
