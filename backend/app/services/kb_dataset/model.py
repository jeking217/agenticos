"""The model: what a dataset's columns mean, agreed before rows arrive.

The model is versioned separately from the data. A model change does not make a
new data version - nothing about the rows changed - and a data import does not
make a new model version. That separation is what lets an old answer be read
with the vocabulary it was asked in while the current rows are explained with
today's.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, BadRequestError, NotFoundError
from app.db.models.kb_dataset import Dataset, pool_for
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import DatasetFieldInput, DatasetMetricInput


def field_payload(field: DatasetFieldInput, ordinal: int) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "name": field.name,
        "display_name": field.display_name or field.name,
        "data_type": field.data_type,
        "is_nullable": field.is_nullable,
        "is_primary_key": field.is_primary_key,
        "synonyms": list(field.synonyms),
        "format_hint": field.format_hint,
        "description": field.description,
    }


def metric_payload(metric: DatasetMetricInput) -> dict[str, Any]:
    return {
        "name": metric.name,
        "display_name": metric.display_name or metric.name,
        "agg": metric.agg,
        "field_name": metric.field_name,
        "format_hint": metric.format_hint,
        "description": metric.description,
    }


def _check_key_fields(fields: Sequence[DatasetFieldInput]) -> list[str]:
    keys = [field.name for field in fields if field.is_primary_key]
    if not keys:
        raise BadRequestError(message="A dataset needs at least one primary-key field")
    return keys


def _check_metrics(
    metrics: Sequence[DatasetMetricInput], fields: Sequence[DatasetFieldInput]
) -> None:
    names = {field.name for field in fields}
    for metric in metrics:
        if metric.field_name and metric.field_name not in names:
            raise BadRequestError(
                message=f"Metric `{metric.name}` aggregates `{metric.field_name}`, which is not a field"
            )


async def create_dataset(
    db: AsyncSession,
    *,
    knowledge_base_id: uuid.UUID,
    organization_id: uuid.UUID,
    name: str,
    fields: Sequence[DatasetFieldInput],
    metrics: Sequence[DatasetMetricInput] = (),
    sheet_name: str | None = None,
    source_filename: str | None = None,
    note: str | None = None,
    created_by: uuid.UUID | None = None,
) -> Dataset:
    if await repo.find_dataset(db, knowledge_base_id, name) is not None:
        raise AlreadyExistsError(message=f"A dataset named `{name}` already exists here")
    keys = _check_key_fields(fields)
    _check_metrics(metrics, fields)

    # The id is chosen here rather than by the column default because the
    # partition key is derived from it, and a row written to the wrong pool is a
    # row no query will find.
    dataset_id = uuid.uuid4()
    dataset = await repo.create_dataset(
        db,
        id=dataset_id,
        knowledge_base_id=knowledge_base_id,
        organization_id=organization_id,
        name=name,
        sheet_name=sheet_name,
        source_filename=source_filename,
        primary_key_fields=keys,
        current_version_no=0,
        model_version=1,
        storage_mode="pooled",
        storage_partition_key=pool_for(dataset_id),
        row_count=0,
        revision=1,
    )
    await repo.replace_fields(
        db, dataset_id, [field_payload(field, index) for index, field in enumerate(fields)]
    )
    await repo.replace_metrics(db, dataset_id, [metric_payload(metric) for metric in metrics])
    await repo.add_model_snapshot(
        db,
        dataset_id=dataset_id,
        version_no=1,
        fields=[field_payload(field, index) for index, field in enumerate(fields)],
        metrics=[metric_payload(metric) for metric in metrics],
        note=note or "Initial model",
        created_by=created_by,
    )
    return dataset


async def update_model(
    db: AsyncSession,
    dataset: Dataset,
    *,
    fields: Sequence[DatasetFieldInput],
    metrics: Sequence[DatasetMetricInput],
    note: str | None,
    actor_user_id: uuid.UUID | None,
) -> Dataset:
    """Replace the field list, bumping the model version once.

    The primary key is frozen once rows exist: the key is the identity a record
    is followed by across versions, and changing it would silently re-point
    every existing row at a different record.
    """
    keys = _check_key_fields(fields)
    _check_metrics(metrics, fields)
    if dataset.current_version_no > 0 and keys != list(dataset.primary_key_fields):
        raise BadRequestError(
            message=(
                "The primary key cannot change once data has been imported; "
                "create a new dataset instead"
            )
        )

    await repo.replace_fields(
        db, dataset.id, [field_payload(field, index) for index, field in enumerate(fields)]
    )
    await repo.replace_metrics(db, dataset.id, [metric_payload(metric) for metric in metrics])

    model_version = dataset.model_version + 1
    await repo.add_model_snapshot(
        db,
        dataset_id=dataset.id,
        version_no=model_version,
        fields=[field_payload(field, index) for index, field in enumerate(fields)],
        metrics=[metric_payload(metric) for metric in metrics],
        note=note,
        created_by=actor_user_id,
    )
    await repo.touch_dataset(db, dataset.id, model_version=model_version)
    dataset.model_version = model_version
    dataset.primary_key_fields = keys
    # `touch_dataset` is a statement, not an attribute write: it sets
    # `updated_at = now()` and `revision = revision + 1` in SQL, leaving both
    # expired on the object. Reading them to build a response is IO, so it has to
    # happen here, awaited - otherwise `PUT /kb/datasets/{id}/model` answered with
    # `MissingGreenlet` instead of the row (#T29).
    await db.refresh(dataset)
    return dataset


async def snapshot(db: AsyncSession, dataset: Dataset, version_no: int) -> dict[str, Any]:
    model = await repo.get_model_snapshot(db, dataset.id, version_no)
    if model is None:
        raise NotFoundError(message="No model at that version")
    return {"version_no": model.version_no, "fields": model.fields, "metrics": model.metrics}


async def field_types(db: AsyncSession, dataset: Dataset) -> dict[str, str]:
    fields = await repo.list_fields(db, dataset.id)
    return {field.name: field.data_type for field in fields}


async def primary_key(db: AsyncSession, dataset: Dataset) -> list[str]:
    fields = await repo.list_fields(db, dataset.id)
    keys = [field.name for field in fields if field.is_primary_key]
    return keys or list(dataset.primary_key_fields)
