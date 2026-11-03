"""Maintenance: a person changing a row, without rewriting history.

Every action here is a version, and every version keeps what was there before.
A deletion is not a gap: it closes the live interval and opens a tombstone, so
a read at a later version knows the record was removed *and* what it said -
which is what a report that cited it needs in order to explain itself.

A row's identity is its `record_key`, not the interval's id: the id belongs to
one interval, and a record has as many of those as it has changes.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.db.models.kb_dataset import Dataset
from app.repositories import kb_dataset as repo
from app.services.kb_dataset.versions import canonical, maintenance_version, normalize, record_key


async def _live_row(db: AsyncSession, dataset: Dataset, key: str) -> Any:
    row = await repo.row_at_version(
        db,
        partition_key=dataset.storage_partition_key,
        dataset_id=dataset.id,
        record_key=key,
        version_no=None,
    )
    if row is None:
        raise NotFoundError(message="No such row in this dataset")
    return row


def _guard_version(dataset: Dataset, expected_version: int | None) -> None:
    if expected_version is not None and dataset.current_version_no != expected_version:
        raise ConflictError(
            message=(
                f"The dataset is at version {dataset.current_version_no}, not "
                f"{expected_version}; reload before editing"
            )
        )


async def update_row(
    db: AsyncSession,
    dataset: Dataset,
    *,
    record_key_value: str,
    values: dict[str, Any],
    reason: str,
    actor_user_id: uuid.UUID | None,
    expected_version: int | None = None,
) -> int:
    _guard_version(dataset, expected_version)
    current = await _live_row(db, dataset, record_key_value)
    if current.is_deleted:
        raise BadRequestError(message="This row was deleted; restore it before editing it")

    fields = {field.name: field for field in await repo.list_fields(db, dataset.id)}
    unknown = [name for name in values if name not in fields]
    if unknown:
        raise BadRequestError(message=f"Unknown field(s): {', '.join(sorted(unknown))}")

    key_fields = list(dataset.primary_key_fields)
    merged = dict(current.values)
    for name, value in values.items():
        normalized = normalize(value)
        if name in key_fields and canonical({name: normalized}) != canonical(
            {name: normalize(current.values.get(name))}
        ):
            # Changing the key would silently make this a different record while
            # looking like an edit; deleting one and adding the other says what
            # actually happened and keeps both histories.
            raise BadRequestError(
                message=(
                    "A primary-key field cannot be edited; delete this row and add the "
                    "corrected one instead"
                )
            )
        merged[name] = normalized

    if canonical(merged) == canonical(current.values):
        return dataset.current_version_no

    return await maintenance_version(
        db,
        dataset,
        operation="update",
        record_key_value=record_key_value,
        before=dict(current.values),
        after=merged,
        reason=reason,
        actor_user_id=actor_user_id,
        close_ids=[current.id],
        values=merged,
    )


async def delete_row(
    db: AsyncSession,
    dataset: Dataset,
    *,
    record_key_value: str,
    reason: str,
    actor_user_id: uuid.UUID | None,
    expected_version: int | None = None,
) -> int:
    _guard_version(dataset, expected_version)
    current = await _live_row(db, dataset, record_key_value)
    if current.is_deleted:
        raise BadRequestError(message="This row is already deleted")
    return await maintenance_version(
        db,
        dataset,
        operation="delete",
        record_key_value=record_key_value,
        before=dict(current.values),
        after=None,
        reason=reason,
        actor_user_id=actor_user_id,
        close_ids=[current.id],
        values=dict(current.values),
        is_deleted=True,
    )


async def restore_row(
    db: AsyncSession,
    dataset: Dataset,
    *,
    record_key_value: str,
    reason: str,
    actor_user_id: uuid.UUID | None,
    expected_version: int | None = None,
) -> int:
    _guard_version(dataset, expected_version)
    current = await _live_row(db, dataset, record_key_value)
    if not current.is_deleted:
        raise BadRequestError(message="This row is not deleted")
    return await maintenance_version(
        db,
        dataset,
        operation="restore",
        record_key_value=record_key_value,
        before=None,
        after=dict(current.values),
        reason=reason,
        actor_user_id=actor_user_id,
        close_ids=[current.id],
        values=dict(current.values),
        is_deleted=False,
    )


async def insert_row(
    db: AsyncSession,
    dataset: Dataset,
    *,
    values: dict[str, Any],
    reason: str,
    actor_user_id: uuid.UUID | None,
    expected_version: int | None = None,
) -> int:
    """Add a record that was never in any file.

    It becomes a new interval at the next version, with its own key, and from
    then on behaves exactly like an imported row - including being kept when a
    later file does not mention it.
    """
    _guard_version(dataset, expected_version)
    fields = {field.name: field for field in await repo.list_fields(db, dataset.id)}
    unknown = [name for name in values if name not in fields]
    if unknown:
        raise BadRequestError(message=f"Unknown field(s): {', '.join(sorted(unknown))}")

    cleaned = {name: normalize(value) for name, value in values.items()}
    key = record_key(cleaned, list(dataset.primary_key_fields))
    if not key.strip("\x1f"):
        raise BadRequestError(message="The primary key fields must be filled in")
    if (
        await repo.row_at_version(
            db,
            partition_key=dataset.storage_partition_key,
            dataset_id=dataset.id,
            record_key=key,
            version_no=None,
        )
        is not None
    ):
        raise ConflictError(message="A row with that key already exists")

    return await maintenance_version(
        db,
        dataset,
        operation="insert",
        record_key_value=key,
        before=None,
        after=cleaned,
        reason=reason,
        actor_user_id=actor_user_id,
        values=cleaned,
    )


async def snapshot_at(
    db: AsyncSession, dataset: Dataset, *, record_key_value: str, version_no: int
) -> dict[str, Any] | None:
    """What this record said at a version - the answer behind an old citation."""
    row = await repo.row_at_version(
        db,
        partition_key=dataset.storage_partition_key,
        dataset_id=dataset.id,
        record_key=record_key_value,
        version_no=version_no,
    )
    if row is None:
        return None
    return {
        "record_key": row.record_key,
        "record_version": row.record_version,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "is_deleted": row.is_deleted,
        "values": dict(row.values),
        "as_of": version_no,
        "observed_at": datetime.now(UTC).isoformat(),
    }
