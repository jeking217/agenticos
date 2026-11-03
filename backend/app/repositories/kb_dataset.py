"""Queries over the `kb_data` tables.

Two rules shape every function here:

- `partition_key` is always in the predicate. It is the partition key, so
  omitting it does not merely cost a scan - it makes the planner read every
  dataset's history to answer a question about one.
- Nothing in this module commits. The request's session commits once, at the
  end, which is what makes a publish either wholly visible or not at all.
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.kb_dataset import (
    Dataset,
    DatasetField,
    DatasetImport,
    DatasetMetric,
    DatasetModel,
    DatasetOperation,
    DatasetRelation,
    DatasetRow,
    DatasetVersion,
    DatasetVersionPin,
)

# --- datasets --------------------------------------------------------------


async def get_dataset(db: AsyncSession, dataset_id: uuid.UUID) -> Dataset | None:
    return await db.get(Dataset, dataset_id)


async def get_dataset_for_update(db: AsyncSession, dataset_id: uuid.UUID) -> Dataset | None:
    """The dataset row, locked, so two publishes cannot pick the same version."""
    result = await db.execute(select(Dataset).where(Dataset.id == dataset_id).with_for_update())
    return result.scalar_one_or_none()


async def get_dataset_by_kb(db: AsyncSession, kb_id: uuid.UUID) -> list[Dataset]:
    result = await db.execute(
        select(Dataset).where(Dataset.knowledge_base_id == kb_id).order_by(Dataset.name)
    )
    return list(result.scalars())


async def find_dataset(db: AsyncSession, kb_id: uuid.UUID, name: str) -> Dataset | None:
    result = await db.execute(
        select(Dataset).where(Dataset.knowledge_base_id == kb_id, Dataset.name == name)
    )
    return result.scalar_one_or_none()


async def list_datasets_for_organizations(
    db: AsyncSession, organization_ids: Sequence[uuid.UUID], *, kb_ids: Sequence[uuid.UUID]
) -> list[Dataset]:
    if not kb_ids:
        return []
    result = await db.execute(
        select(Dataset)
        .where(
            Dataset.knowledge_base_id.in_(list(kb_ids)),
            Dataset.organization_id.in_(list(organization_ids)),
            Dataset.archived_at.is_(None),
        )
        .order_by(Dataset.name)
    )
    return list(result.scalars())


async def create_dataset(db: AsyncSession, **values: Any) -> Dataset:
    dataset = Dataset(**values)
    db.add(dataset)
    await db.flush()
    return dataset


async def touch_dataset(
    db: AsyncSession,
    dataset_id: uuid.UUID,
    *,
    version_no: int | None = None,
    row_count: int | None = None,
    model_version: int | None = None,
    last_import_at: Any = None,
) -> None:
    values: dict[str, Any] = {"revision": Dataset.revision + 1}
    if version_no is not None:
        values["current_version_no"] = version_no
    if row_count is not None:
        values["row_count"] = row_count
    if model_version is not None:
        values["model_version"] = model_version
    if last_import_at is not None:
        values["last_import_at"] = last_import_at
    await db.execute(update(Dataset).where(Dataset.id == dataset_id).values(**values))


async def archive_dataset(db: AsyncSession, dataset_id: uuid.UUID, when: Any) -> None:
    await db.execute(
        update(Dataset)
        .where(Dataset.id == dataset_id)
        .values(archived_at=when, storage_mode="archived")
    )


# --- model -----------------------------------------------------------------


async def list_fields(db: AsyncSession, dataset_id: uuid.UUID) -> list[DatasetField]:
    result = await db.execute(
        select(DatasetField)
        .where(DatasetField.dataset_id == dataset_id)
        .order_by(DatasetField.ordinal)
    )
    return list(result.scalars())


async def list_metrics(db: AsyncSession, dataset_id: uuid.UUID) -> list[DatasetMetric]:
    result = await db.execute(
        select(DatasetMetric)
        .where(DatasetMetric.dataset_id == dataset_id)
        .order_by(DatasetMetric.name)
    )
    return list(result.scalars())


async def replace_fields(
    db: AsyncSession, dataset_id: uuid.UUID, fields: Iterable[dict[str, Any]]
) -> None:
    """Rewrite a dataset's field list.

    `ordinal` comes from the payload when it carries one - `field_payload` does,
    because the field list is also what an old query is explained in and its order
    is part of that model - and from the iteration order otherwise.
    """
    await db.execute(delete(DatasetField).where(DatasetField.dataset_id == dataset_id))
    for ordinal, field in enumerate(fields):
        db.add(DatasetField(dataset_id=dataset_id, **{"ordinal": ordinal, **field}))
    await db.flush()


async def replace_metrics(
    db: AsyncSession, dataset_id: uuid.UUID, metrics: Iterable[dict[str, Any]]
) -> None:
    await db.execute(delete(DatasetMetric).where(DatasetMetric.dataset_id == dataset_id))
    for metric in metrics:
        db.add(DatasetMetric(dataset_id=dataset_id, **metric))
    await db.flush()


async def add_model_snapshot(
    db: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    version_no: int,
    fields: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    note: str | None,
    created_by: uuid.UUID | None,
) -> DatasetModel:
    model = DatasetModel(
        dataset_id=dataset_id,
        version_no=version_no,
        fields=fields,
        metrics=metrics,
        note=note,
        created_by=created_by,
    )
    db.add(model)
    await db.flush()
    return model


async def get_model_snapshot(
    db: AsyncSession, dataset_id: uuid.UUID, version_no: int
) -> DatasetModel | None:
    result = await db.execute(
        select(DatasetModel).where(
            DatasetModel.dataset_id == dataset_id, DatasetModel.version_no == version_no
        )
    )
    return result.scalar_one_or_none()


# --- versions --------------------------------------------------------------


async def add_version(db: AsyncSession, **values: Any) -> DatasetVersion:
    version = DatasetVersion(**values)
    db.add(version)
    await db.flush()
    return version


async def list_versions(
    db: AsyncSession, dataset_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> tuple[list[DatasetVersion], int]:
    total = await db.scalar(
        select(func.count())
        .select_from(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
    )
    result = await db.execute(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_no.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars()), int(total or 0)


async def get_version(
    db: AsyncSession, dataset_id: uuid.UUID, version_no: int
) -> DatasetVersion | None:
    result = await db.execute(
        select(DatasetVersion).where(
            DatasetVersion.dataset_id == dataset_id, DatasetVersion.version_no == version_no
        )
    )
    return result.scalar_one_or_none()


async def latest_import_version(db: AsyncSession, dataset_id: uuid.UUID) -> DatasetVersion | None:
    result = await db.execute(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_no.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# --- rows ------------------------------------------------------------------


def _scope(partition_key: uuid.UUID, dataset_id: uuid.UUID, version_no: int | None):
    """The predicate every read starts with: one partition, one dataset, one version."""
    clauses = [DatasetRow.partition_key == partition_key, DatasetRow.dataset_id == dataset_id]
    if version_no is None:
        clauses.append(DatasetRow.valid_to.is_(None))
    else:
        clauses.append(DatasetRow.valid_from <= version_no)
        clauses.append((DatasetRow.valid_to.is_(None)) | (DatasetRow.valid_to > version_no))
    return clauses


async def current_rows(
    db: AsyncSession,
    *,
    partition_key: uuid.UUID,
    dataset_id: uuid.UUID,
    for_update: bool = False,
) -> list[DatasetRow]:
    statement = select(DatasetRow).where(*_scope(partition_key, dataset_id, None))
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    return list(result.scalars())


async def rows_as_of(
    db: AsyncSession,
    *,
    partition_key: uuid.UUID,
    dataset_id: uuid.UUID,
    version_no: int,
) -> list[DatasetRow]:
    result = await db.execute(
        select(DatasetRow).where(*_scope(partition_key, dataset_id, version_no))
    )
    return list(result.scalars())


async def row_at_version(
    db: AsyncSession,
    *,
    partition_key: uuid.UUID,
    dataset_id: uuid.UUID,
    record_key: str,
    version_no: int | None,
) -> DatasetRow | None:
    clauses = [*_scope(partition_key, dataset_id, version_no), DatasetRow.record_key == record_key]
    result = await db.execute(select(DatasetRow).where(*clauses))
    return result.scalars().first()


async def insert_rows(db: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    await db.execute(DatasetRow.__table__.insert(), list(rows))


CLOSE_BATCH = 1_000
"""How many intervals one `close_rows` statement may end.

Measured, not guessed. A re-import of one million rows changes fifty thousand
intervals, and closing them in a single `UPDATE … WHERE id IN (50 000 bind
parameters)` took **1 890 seconds** - 90% of a re-import that otherwise costs
three minutes. Chunked into thousands it is a thousand normal index-driven
statements. `tests/integration/test_kb_dataset_capacity.py` is the run that
found it; `tests/integration/test_kb_dataset_publish_batches.py` pins the
behaviour across a batch boundary, which is where a loop like this goes wrong.
"""


async def close_rows(
    db: AsyncSession, *, partition_key: uuid.UUID, row_ids: Sequence[uuid.UUID], version_no: int
) -> None:
    """End an interval. This is the only UPDATE the store performs.

    `values` is never touched: a version that changed a row appears as a closed
    interval plus a new one, so the row as it was at v3 still reads exactly as
    it was at v3.

    Batched because the planner does badly with a bind list in the tens of
    thousands - see `CLOSE_BATCH`. Every batch carries the partition predicate, so
    partition pruning still picks one partition per statement.
    """
    if not row_ids:
        return
    ids = list(row_ids)
    for start in range(0, len(ids), CLOSE_BATCH):
        await db.execute(
            update(DatasetRow)
            .where(
                DatasetRow.partition_key == partition_key,
                DatasetRow.id.in_(ids[start : start + CLOSE_BATCH]),
                DatasetRow.valid_to.is_(None),
            )
            .values(valid_to=version_no, updated_at=func.now())
        )


async def count_current(
    db: AsyncSession, *, partition_key: uuid.UUID, dataset_id: uuid.UUID
) -> int:
    total = await db.scalar(
        select(func.count())
        .select_from(DatasetRow)
        .where(*_scope(partition_key, dataset_id, None), DatasetRow.is_deleted.is_(False))
    )
    return int(total or 0)


async def count_rows_at(
    db: AsyncSession, *, partition_key: uuid.UUID, dataset_id: uuid.UUID, version_no: int
) -> int:
    total = await db.scalar(
        select(func.count())
        .select_from(DatasetRow)
        .where(*_scope(partition_key, dataset_id, version_no), DatasetRow.is_deleted.is_(False))
    )
    return int(total or 0)


# --- relations -------------------------------------------------------------


async def list_relations(db: AsyncSession, dataset_id: uuid.UUID) -> list[DatasetRelation]:
    result = await db.execute(
        select(DatasetRelation)
        .where(DatasetRelation.dataset_id == dataset_id)
        .order_by(DatasetRelation.name)
    )
    return list(result.scalars())


async def get_relation(
    db: AsyncSession, dataset_id: uuid.UUID, name: str
) -> DatasetRelation | None:
    result = await db.execute(
        select(DatasetRelation).where(
            DatasetRelation.dataset_id == dataset_id, DatasetRelation.name == name
        )
    )
    return result.scalar_one_or_none()


async def get_relation_by_id(db: AsyncSession, relation_id: uuid.UUID) -> DatasetRelation | None:
    return await db.get(DatasetRelation, relation_id)


async def create_relation(db: AsyncSession, **values: Any) -> DatasetRelation:
    relation = DatasetRelation(**values)
    db.add(relation)
    await db.flush()
    return relation


async def delete_relation(db: AsyncSession, relation_id: uuid.UUID) -> None:
    await db.execute(delete(DatasetRelation).where(DatasetRelation.id == relation_id))


# --- imports ---------------------------------------------------------------


async def create_import(db: AsyncSession, **values: Any) -> DatasetImport:
    record = DatasetImport(**values)
    db.add(record)
    await db.flush()
    return record


async def get_import(db: AsyncSession, import_id: uuid.UUID) -> DatasetImport | None:
    return await db.get(DatasetImport, import_id)


async def list_imports(
    db: AsyncSession, kb_id: uuid.UUID, *, skip: int = 0, limit: int = 20
) -> tuple[list[DatasetImport], int]:
    total = await db.scalar(
        select(func.count())
        .select_from(DatasetImport)
        .where(DatasetImport.knowledge_base_id == kb_id)
    )
    result = await db.execute(
        select(DatasetImport)
        .where(DatasetImport.knowledge_base_id == kb_id)
        .order_by(DatasetImport.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars()), int(total or 0)


# --- pins and operations ---------------------------------------------------


async def pin_versions(db: AsyncSession, pins: Sequence[dict[str, Any]]) -> None:
    if not pins:
        return
    for pin in pins:
        db.add(DatasetVersionPin(**pin))
    await db.flush()


async def pinned_versions(db: AsyncSession, dataset_id: uuid.UUID) -> set[int]:
    result = await db.execute(
        select(DatasetVersionPin.version_no).where(DatasetVersionPin.dataset_id == dataset_id)
    )
    return {int(v) for v in result.scalars()}


async def add_operation(db: AsyncSession, **values: Any) -> DatasetOperation:
    operation = DatasetOperation(**values)
    db.add(operation)
    await db.flush()
    return operation


async def list_operations(
    db: AsyncSession, dataset_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> tuple[list[DatasetOperation], int]:
    total = await db.scalar(
        select(func.count())
        .select_from(DatasetOperation)
        .where(DatasetOperation.dataset_id == dataset_id)
    )
    result = await db.execute(
        select(DatasetOperation)
        .where(DatasetOperation.dataset_id == dataset_id)
        .order_by(DatasetOperation.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars()), int(total or 0)


async def page_rows(
    db: AsyncSession,
    *,
    partition_key: uuid.UUID,
    dataset_id: uuid.UUID,
    version_no: int | None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
    include_deleted: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """One page of rows as they stood at a version, optionally text-filtered.

    Raw SQL rather than the ORM because the filter is over the JSONB document:
    `values::text ILIKE` is a scan, and pretending otherwise by loading every
    row and filtering in Python would be the same scan with more memory.
    """
    clauses = ["partition_key = :pk", "dataset_id = :ds"]
    params: dict[str, Any] = {"pk": partition_key, "ds": dataset_id}
    if version_no is None:
        clauses.append("valid_to IS NULL")
    else:
        clauses.append("valid_from <= :version")
        clauses.append("(valid_to IS NULL OR valid_to > :version)")
        params["version"] = version_no
    if not include_deleted:
        clauses.append("is_deleted = false")
    if search:
        clauses.append("values::text ILIKE :search")
        params["search"] = f"%{search}%"

    where = " AND ".join(clauses)
    total = await db.scalar(
        text(f"SELECT count(*) FROM kb_data.dataset_rows WHERE {where}"), params
    )
    rows = await db.execute(
        text(
            f"""
            SELECT record_key, record_version, valid_from, valid_to, is_deleted, values
            FROM kb_data.dataset_rows
            WHERE {where}
            ORDER BY record_key
            LIMIT :limit OFFSET :skip
            """
        ),
        {**params, "limit": limit, "skip": skip},
    )
    return [dict(row._mapping) for row in rows], int(total or 0)


# --- retention -------------------------------------------------------------


async def datasets_over_version_limit(
    db: AsyncSession, *, keep: int, limit: int
) -> list[uuid.UUID]:
    """Datasets holding more versions than `keep`, oldest first.

    Counted in the database rather than by loading every dataset: the sweep runs
    over a whole deployment, and the overwhelming majority of datasets are well
    inside the window. `keep` is passed as a bound, never interpolated.
    """
    rows = await db.execute(
        text(
            """
            SELECT d.id
            FROM kb_data.datasets d
            JOIN (
                SELECT dataset_id, count(*) AS held
                FROM kb_data.dataset_versions
                GROUP BY dataset_id
            ) v ON v.dataset_id = d.id
            WHERE v.held > :keep
            ORDER BY d.id
            LIMIT :limit
            """
        ),
        {"keep": keep, "limit": limit},
    )
    return [row[0] for row in rows]


# --- storage tier ----------------------------------------------------------


async def pooled_row_counts(db: AsyncSession) -> dict[uuid.UUID, int]:
    """Current live rows per dataset, for the datasets still on a shared pool.

    Counted in one statement over the whole deployment rather than a `count` per
    dataset: the promotion sweep runs daily over every dataset in the
    installation, and this is the query whose result decides which of them get a
    relation of their own. Historical versions are left out on purpose - a
    dataset is heavy because of the rows a query reads today, not because of the
    versions retention has not pruned yet.
    """
    result = await db.execute(
        select(DatasetRow.dataset_id, func.count())
        .join(Dataset, Dataset.id == DatasetRow.dataset_id)
        .where(
            Dataset.storage_mode == "pooled",
            DatasetRow.valid_to.is_(None),
            DatasetRow.is_deleted.is_(False),
        )
        .group_by(DatasetRow.dataset_id)
    )
    return {row[0]: int(row[1]) for row in result.all()}


async def set_storage(
    db: AsyncSession, dataset_id: uuid.UUID, *, mode: str, partition_key: uuid.UUID
) -> None:
    """Point a dataset at the relation that now holds it.

    Called inside the transaction that moved the rows, so the pointer and the
    rows cannot disagree: a commit that landed one without the other would leave
    the dataset reading a partition it no longer writes to.
    """
    await db.execute(
        update(Dataset)
        .where(Dataset.id == dataset_id)
        .values(
            storage_mode=mode,
            storage_partition_key=partition_key,
            revision=Dataset.revision + 1,
            updated_at=func.now(),
        )
    )
