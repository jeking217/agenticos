"""Publishing rows: the one place a version number is minted.

The rules this module encodes, in the order they matter:

1. A version only ever adds. Publishing closes the intervals of the rows it
   changes and opens new ones; no `values` is rewritten, so a read at v3 says
   what v3 said even after v9 exists.
2. A row missing from a new file is *not* deleted. It keeps its interval and its
   own `record_version`, and a read at the newer version still returns it. That
   is the difference between "the file stopped listing it" and "we removed it",
   and only a person can say which one happened - through maintenance, which
   writes a tombstone.
3. A publish that changes nothing does not mint a version. Re-uploading the same
   workbook is a no-op rather than a version history full of identical entries.
"""

import hashlib
import json
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.kb_dataset import Dataset
from app.repositories import kb_dataset as repo
from app.services.kb_dataset import metrics


def normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFKC", value).strip()
    return value


def record_key(values: dict[str, Any], key_fields: Sequence[str]) -> str:
    """The identity of a record, independent of which version mentions it.

    Text is case-folded and normalised so "ACM E" from one export and "acm e"
    from another are one customer rather than two - the same reason a person
    would call them one.
    """
    parts: list[str] = []
    for name in key_fields:
        value = normalize(values.get(name))
        if value is None:
            parts.append("")
        elif isinstance(value, bool):
            parts.append("true" if value else "false")
        elif isinstance(value, float) and value.is_integer():
            parts.append(str(int(value)))
        elif isinstance(value, str):
            parts.append(value.casefold())
        else:
            parts.append(str(value))
    return "\x1f".join(parts)


def canonical(values: dict[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, ensure_ascii=False, default=str)


def digest_rows(rows: Sequence[dict[str, Any]]) -> str:
    hasher = hashlib.sha256()
    for row in sorted(rows, key=canonical):
        hasher.update(canonical(row).encode())
        hasher.update(b"\n")
    return hasher.hexdigest()


@dataclass
class PublishResult:
    version_no: int
    published: bool
    rows_added: int = 0
    rows_changed: int = 0
    rows_removed: int = 0
    rows_unchanged: int = 0
    skipped: int = 0
    duplicates: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.rows_added == 0 and self.rows_changed == 0 and self.rows_removed == 0


async def publish(
    db: AsyncSession,
    dataset: Dataset,
    rows: Sequence[dict[str, Any]],
    *,
    key_fields: Sequence[str],
    kind: str = "import",
    source_filename: str | None = None,
    sheet_name: str | None = None,
    import_id: uuid.UUID | None = None,
    note: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    force: bool = False,
) -> PublishResult:
    """Fold `rows` into `dataset` as the next version, and say what changed.

    `dataset` must already be locked by the caller (`repo.get_dataset_for_update`)
    - two concurrent publishes that each read `current_version_no` before the
    other wrote would otherwise mint the same number twice.
    """
    if not key_fields:
        raise ValueError("A dataset cannot be published without a primary key")

    normalized: dict[str, dict[str, Any]] = {}
    skipped = 0
    duplicates = 0
    for row in rows:
        values = {name: normalize(value) for name, value in row.items()}
        key = record_key(values, key_fields)
        if not key.strip("\x1f"):
            skipped += 1
            continue
        if key in normalized:
            duplicates += 1
        normalized[key] = values

    current = await repo.current_rows(
        db, partition_key=dataset.storage_partition_key, dataset_id=dataset.id, for_update=True
    )
    by_key = {row.record_key: row for row in current}

    version_no = dataset.current_version_no + 1
    added = changed = unchanged = 0
    to_close: list[uuid.UUID] = []
    to_insert: list[dict[str, Any]] = []

    for key, values in normalized.items():
        existing = by_key.get(key)
        if existing is None:
            added += 1
            to_insert.append(_interval(dataset, key, values, version_no, record_version=version_no))
        elif canonical(existing.values) == canonical(values):
            unchanged += 1
        else:
            changed += 1
            to_close.append(existing.id)
            to_insert.append(_interval(dataset, key, values, version_no, record_version=version_no))

    # Everything in `by_key` and not in `normalized` is a row the file stopped
    # mentioning. It is left open on purpose (rule 2 above), and counted so the
    # report can say so rather than silently under-report the change.
    missing = len([key for key in by_key if key not in normalized])

    result = PublishResult(
        version_no=version_no,
        published=False,
        rows_added=added,
        rows_changed=changed,
        rows_removed=0,
        rows_unchanged=unchanged,
        skipped=skipped,
        duplicates=duplicates,
    )
    if skipped:
        result.warnings.append(
            f"{skipped} row(s) have no value in the primary key and were skipped"
        )
    if duplicates:
        result.warnings.append(f"{duplicates} duplicate key(s) kept the last occurrence")
    if missing:
        result.warnings.append(
            f"{missing} record(s) in the previous version are absent here; they were kept at "
            "their existing version rather than deleted"
        )

    if unchanged == len(normalized) and not force and kind == "import":
        # Same workbook as last time: nothing to publish, and no version to mint.
        await repo.touch_dataset(
            db,
            dataset.id,
            last_import_at=datetime.now(UTC),
        )
        return result

    if to_close:
        await repo.close_rows(
            db,
            partition_key=dataset.storage_partition_key,
            row_ids=to_close,
            version_no=version_no,
        )
    await repo.insert_rows(db, to_insert)

    await repo.add_version(
        db,
        dataset_id=dataset.id,
        version_no=version_no,
        kind=kind,
        source_filename=source_filename,
        sheet_name=sheet_name,
        import_id=import_id,
        rows_added=added,
        rows_changed=changed,
        rows_removed=0,
        rows_unchanged=unchanged,
        stats={
            "digest": digest_rows(list(normalized.values())),
            "missing": missing,
            "skipped": skipped,
            "duplicates": duplicates,
            "warnings": result.warnings,
        },
        note=note,
        published_by=actor_user_id,
    )

    row_count = await repo.count_current(
        db, partition_key=dataset.storage_partition_key, dataset_id=dataset.id
    )
    await repo.touch_dataset(
        db,
        dataset.id,
        version_no=version_no,
        row_count=row_count,
        last_import_at=datetime.now(UTC),
    )

    result.published = True
    dataset.current_version_no = version_no
    dataset.row_count = row_count
    # The growth curve retention is steered by: one row per published version, not
    # one per interval written.
    metrics.version_published(rows=row_count, version_no=version_no)
    return result


def _interval(
    dataset: Dataset,
    key: str,
    values: dict[str, Any],
    version_no: int,
    *,
    record_version: int,
    is_deleted: bool = False,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "partition_key": dataset.storage_partition_key,
        "id": uuid.uuid4(),
        "dataset_id": dataset.id,
        "record_key": key,
        "record_version": record_version,
        "valid_from": version_no,
        "valid_to": None,
        "is_deleted": is_deleted,
        "values": values,
        "created_at": now,
        "updated_at": now,
    }


async def maintenance_version(
    db: AsyncSession,
    dataset: Dataset,
    *,
    operation: str,
    record_key_value: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str,
    actor_user_id: uuid.UUID | None,
    close_ids: Sequence[uuid.UUID] = (),
    values: dict[str, Any] | None = None,
    is_deleted: bool = False,
    note: str | None = None,
) -> int:
    """One person's edit, deletion or restoration, as its own version.

    A maintenance action gets a version for the same reason an import does: it
    changed what the data says, and an answer computed today should be
    explainable tomorrow by pointing at the version that made it so.
    """
    version_no = dataset.current_version_no + 1
    if close_ids:
        await repo.close_rows(
            db,
            partition_key=dataset.storage_partition_key,
            row_ids=close_ids,
            version_no=version_no,
        )
    if values is not None:
        await repo.insert_rows(
            db,
            [
                _interval(
                    dataset,
                    record_key_value,
                    values,
                    version_no,
                    record_version=version_no,
                    is_deleted=is_deleted,
                )
            ],
        )

    await repo.add_version(
        db,
        dataset_id=dataset.id,
        version_no=version_no,
        kind="maintenance" if operation != "restore" else "restore",
        rows_added=1 if operation in {"insert", "restore"} else 0,
        rows_changed=1 if operation == "update" else 0,
        rows_removed=1 if operation == "delete" else 0,
        note=note or reason,
        published_by=actor_user_id,
        stats={"operation": operation, "record_key": record_key_value},
    )
    await repo.add_operation(
        db,
        dataset_id=dataset.id,
        version_no=version_no,
        operation=operation,
        record_key=record_key_value,
        before=before,
        after=after,
        reason=reason,
        actor_user_id=actor_user_id,
    )
    row_count = await repo.count_current(
        db, partition_key=dataset.storage_partition_key, dataset_id=dataset.id
    )
    await repo.touch_dataset(db, dataset.id, version_no=version_no, row_count=row_count)
    dataset.current_version_no = version_no
    dataset.row_count = row_count
    return version_no
