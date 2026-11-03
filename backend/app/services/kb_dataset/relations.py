"""Relations: the joins that are allowed to exist, and the guesses offered.

A relation is a promise the compiler relies on: these two fields mean the same
thing, so a join on them does not multiply rows into nonsense. That promise is
why a relation is validated in both directions before it is stored - the types
have to agree, and the values have to actually overlap - and why the model can
only join along one.

Nothing here guesses silently. A candidate is stored with `status='candidate'`
and a confidence, the wizard draws it as a dashed line, and only a person
confirms it into a join the compiler will accept.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, BadRequestError
from app.db.models.kb_dataset import Dataset, DatasetField
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import DatasetRelationInput

SCHEMA = "kb_data"

# Enough values to tell "these are the same key" from "these are both columns
# called id"; small enough to run on a click without touching the whole table.
_OVERLAP_SAMPLE = 500
# Below this, the columns are probably not the same key even if they are named
# the same. Above it, they are, even if they are not.
_OVERLAP_WARNING = 0.3

_TYPE_FAMILIES = {
    "text": "text",
    "integer": "number",
    "numeric": "number",
    "date": "temporal",
    "timestamp": "temporal",
    "boolean": "boolean",
}


def _family(data_type: str) -> str:
    return _TYPE_FAMILIES.get(data_type, "text")


async def _distinct_values(
    db: AsyncSession, dataset: Dataset, field: DatasetField, limit: int = _OVERLAP_SAMPLE
) -> set[str]:
    result = await db.execute(
        text(
            f"""
            SELECT DISTINCT values->>:field AS value
            FROM {SCHEMA}.dataset_rows
            WHERE partition_key = :pk AND dataset_id = :ds AND valid_to IS NULL
              AND is_deleted = false AND values->>:field IS NOT NULL
            LIMIT :limit
            """
        ),
        {
            "field": field.name,
            "pk": dataset.storage_partition_key,
            "ds": dataset.id,
            "limit": limit,
        },
    )
    return {str(row[0]) for row in result if row[0] not in (None, "")}


async def overlap_ratio(
    db: AsyncSession,
    dataset: Dataset,
    field: DatasetField,
    target: Dataset,
    target_field: DatasetField,
) -> float:
    """How much of the smaller side's value set appears on the other side."""
    left = await _distinct_values(db, dataset, field)
    right = await _distinct_values(db, target, target_field)
    if not left or not right:
        return 0.0
    return round(len(left & right) / min(len(left), len(right)), 3)


async def validate(db: AsyncSession, dataset: Dataset, data: DatasetRelationInput) -> list[str]:
    """Everything that must hold before a relation is stored, or a refusal.

    Returns warnings - things a person may accept - rather than raising for
    them. A relation whose values do not overlap is usually a mistake, but a
    newly created target dataset has no rows yet and overlaps with nothing, and
    refusing that would make the wizard unusable in the natural order.
    """
    if data.target_dataset_id == dataset.id:
        raise BadRequestError(message="A dataset cannot be related to itself")
    target = await repo.get_dataset(db, data.target_dataset_id)
    if target is None or target.knowledge_base_id != dataset.knowledge_base_id:
        raise BadRequestError(message="The related dataset is not in this knowledge base")
    if target.archived_at is not None:
        raise BadRequestError(message="The related dataset is archived")

    if await repo.get_relation(db, dataset.id, data.name) is not None:
        raise AlreadyExistsError(message=f"A relation named `{data.name}` already exists")

    local_fields = {field.name: field for field in await repo.list_fields(db, dataset.id)}
    foreign_fields = {field.name: field for field in await repo.list_fields(db, target.id)}
    warnings: list[str] = []

    for local, foreign in zip(data.local_fields, data.foreign_fields, strict=True):
        left = local_fields.get(local)
        right = foreign_fields.get(foreign)
        if left is None:
            raise BadRequestError(message=f"`{local}` is not a field of {dataset.name}")
        if right is None:
            raise BadRequestError(message=f"`{foreign}` is not a field of {target.name}")
        if _family(left.data_type) != _family(right.data_type):
            raise BadRequestError(
                message=(
                    f"`{local}` is {left.data_type} and `{foreign}` is {right.data_type}; "
                    "a join between them would compare two different kinds of value"
                )
            )

    if data.status == "confirmed":
        ratio = await overlap_ratio(
            db,
            dataset,
            local_fields[data.local_fields[0]],
            target,
            foreign_fields[data.foreign_fields[0]],
        )
        if 0 < ratio < _OVERLAP_WARNING:
            warnings.append(
                f"Only {ratio:.0%} of the values in `{data.local_fields[0]}` appear in "
                f"`{target.name}.{data.foreign_fields[0]}`; the join may match little"
            )
    return warnings


async def create(
    db: AsyncSession, dataset: Dataset, data: DatasetRelationInput
) -> tuple[Any, list[str]]:
    warnings = await validate(db, dataset, data)
    relation = await repo.create_relation(
        db,
        dataset_id=dataset.id,
        target_dataset_id=data.target_dataset_id,
        name=data.name,
        cardinality=data.cardinality,
        join_type=data.join_type,
        local_fields=list(data.local_fields),
        foreign_fields=list(data.foreign_fields),
        status=data.status,
        confidence=Decimal("1.000") if data.status == "confirmed" else None,
        description=data.description,
    )
    return relation, warnings


def _name_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if left.endswith(f"_{right}") or right.endswith(f"_{left}"):
        return 0.85
    if left in right or right in left:
        return 0.6
    return 0.0


async def infer_candidates(db: AsyncSession, dataset: Dataset) -> list[dict[str, Any]]:
    """Suggested joins to the other datasets in the same knowledge base.

    Scored on two things a person would also look at: the names line up, and the
    values actually overlap. A pair that scores well is offered as a dashed line
    with its confidence; nothing is written until someone confirms it.
    """
    siblings = [
        candidate
        for candidate in await repo.get_dataset_by_kb(db, dataset.knowledge_base_id)
        if candidate.id != dataset.id and candidate.archived_at is None
    ]
    own_fields = await repo.list_fields(db, dataset.id)
    existing = {relation.name for relation in await repo.list_relations(db, dataset.id)}

    suggestions: list[dict[str, Any]] = []
    for sibling in siblings:
        sibling_fields = await repo.list_fields(db, sibling.id)
        best: dict[str, Any] | None = None
        for left in own_fields:
            for right in sibling_fields:
                if _family(left.data_type) != _family(right.data_type):
                    continue
                similarity = _name_similarity(left.name, right.name)
                if similarity == 0:
                    continue
                ratio = await overlap_ratio(db, dataset, left, sibling, right)
                score = round(similarity * 0.5 + ratio * 0.5, 3)
                if best is None or score > best["confidence"]:
                    best = {
                        "target_dataset_id": str(sibling.id),
                        "target_dataset_name": sibling.name,
                        "name": f"to_{sibling.name}"[:64],
                        "local_fields": [left.name],
                        "foreign_fields": [right.name],
                        "cardinality": "n:1" if ratio >= 0.95 else "1:n",
                        "confidence": score,
                        "overlap": ratio,
                        "already_exists": f"to_{sibling.name}"[:64] in existing,
                    }
        if best and best["confidence"] >= 0.45:
            suggestions.append(best)
    return sorted(suggestions, key=lambda item: item["confidence"], reverse=True)


async def distinct_values_for_ui(
    db: AsyncSession, dataset: Dataset, field_name: str, limit: int = 100
) -> list[str]:
    fields = {field.name: field for field in await repo.list_fields(db, dataset.id)}
    field = fields.get(field_name)
    if field is None:
        raise BadRequestError(message=f"`{field_name}` is not a field of this dataset")
    return sorted(await _distinct_values(db, dataset, field, limit))


async def resolve_join_target(
    db: AsyncSession, dataset: Dataset, relation: Any
) -> tuple[Dataset, list[DatasetField]]:
    target = await repo.get_dataset(db, relation.target_dataset_id)
    if target is None:
        raise BadRequestError(message="The related dataset has been deleted")
    return target, await repo.list_fields(db, target.id)


def as_uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(value)
