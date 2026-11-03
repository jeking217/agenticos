"""Dataset knowledge bases: the service the routes and the agent tools share.

Every method takes an `AuthContext`, resolves the dataset through its knowledge
base, and refuses before doing anything else. A dataset has no separate ACL: it
is a part of a knowledge base, and inheriting that base's reach is what keeps
"who can see this number" answerable in one place.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.db.models.kb_dataset import Dataset
from app.db.models.knowledge_base import KBType, KnowledgeBase
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import (
    DatasetCompareResult,
    DatasetCreate,
    DatasetFieldChange,
    DatasetImportCommit,
    DatasetImportDraft,
    DatasetQuery,
    DatasetQueryResult,
    DatasetRead,
    DatasetRelationInput,
    DatasetRowList,
    DatasetRowRead,
    DatasetUpdate,
    QueryColumn,
)
from app.services.kb_dataset import compare as compare_service
from app.services.kb_dataset import imports as imports_service
from app.services.kb_dataset import maintenance as maintenance_service
from app.services.kb_dataset import metrics as metrics_service
from app.services.kb_dataset import model as model_service
from app.services.kb_dataset import quota as quota_service
from app.services.kb_dataset import relations as relations_service
from app.services.kb_dataset.query import QueryContext, RelationTarget, compile_query, run_query
from app.services.kb_dataset.versions import publish
from app.services.knowledge_base import KnowledgeBaseService


def _require_dataset_kb(kb: KnowledgeBase) -> None:
    if kb.kb_type != KBType.DATASET.value:
        raise BadRequestError(message="This knowledge base holds documents, not datasets")


def _require_enabled() -> None:
    """Refuse to *start* anything while the deployment has the feature off.

    Reads are deliberately not gated: what already exists stays readable, and a
    switch meant to stop a rollout from growing does not also take away the work
    somebody already published. `KB_DATASETS_ENABLED` is the rollout switch, not
    a delete.
    """
    if not settings.KB_DATASETS_ENABLED:
        raise BadRequestError(message="Dataset knowledge bases are turned off in this deployment")


@dataclass(frozen=True)
class Citation:
    """The tool call an answer came from, for `version_pins` and its audit trail.

    All three are optional because a caller may legitimately have only some of
    them - a workflow has no conversation, a script has no message - and a pin
    that protects the version is worth more than a pin that insists on being
    perfectly attributed.
    """

    tool_call_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None


class DatasetService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._knowledge_bases = KnowledgeBaseService(db)

    # -- access -------------------------------------------------------------

    async def kb(self, kb_id: uuid.UUID, ctx: Any, *, write: bool = False) -> KnowledgeBase:
        kb = (
            await self._knowledge_bases.get_for_write(kb_id, ctx=ctx)
            if write
            else await self._knowledge_bases.get(kb_id, ctx=ctx)
        )
        _require_dataset_kb(kb)
        return kb

    async def dataset(self, dataset_id: uuid.UUID, ctx: Any, *, write: bool = False) -> Dataset:
        dataset = await repo.get_dataset(self.db, dataset_id)
        if dataset is None or dataset.archived_at is not None:
            raise NotFoundError(message="No such dataset")
        await self.kb(dataset.knowledge_base_id, ctx, write=write)
        return dataset

    async def _audit_row(
        self,
        ctx: Any,
        dataset: Dataset,
        *,
        action: str,
        record_key: str,
        version_no: int,
        reason: str,
    ) -> None:
        """One entry per hand-made change, naming the record and the version it made.

        The reason is recorded and the values are not. A row's values are already
        in `dataset_operations`, with their before and after, and an audit trail
        that copied them would be a second full copy of the data - one that
        retention does not reach. What the trail is for here is the question the
        operations list cannot answer on its own: this number moved, and this
        person said why.

        `target_id` is the dataset, and the record is named in `details`: the
        column is 36 characters - one uuid - so `f"{dataset.id}:{record_key}"`
        overflowed it and every hand edit raised rather than being recorded
        (#T29). The action already says a row moved, and `record_key` says which.
        """
        await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=dataset.organization_id,
            action=action,
            target_type="dataset_row",
            target_id=str(dataset.id),
            details={
                "dataset_id": str(dataset.id),
                "dataset_name": dataset.name,
                "record_key": record_key,
                "version_no": version_no,
                "reason": reason,
            },
        )

    async def create_base(self, data: Any, ctx: Any) -> KnowledgeBase:
        """Create a base that holds rows, not passages.

        It shares the `knowledge_bases` row - and therefore its owner, visibility,
        grants and `collections:*` permissions - because a dataset is a knowledge
        base and answering "who may read this number" in two places is how the two
        answers drift apart.

        The embedding columns are filled with a sentinel rather than a model:
        there is no collection, nothing is vectorised, and `ingestion` routes
        refuse a dataset base before they could read them. Leaving them empty is
        not an option - the columns are NOT NULL for every other base.
        """
        _require_enabled()
        org_id = ctx.organization_id
        if data.scope == "personal" and ctx.organization_id is None:
            raise BadRequestError(message="A personal base needs an organization context")
        base = KnowledgeBase(
            name=data.name,
            description=data.description,
            scope=data.scope,
            kb_type=KBType.DATASET.value,
            collection_name=f"ds-{uuid.uuid4().hex[:12]}",
            embedding_model="none",
            embedding_dim=1,
            embedding_provider="none",
            owner_user_id=ctx.subject_id,
            organization_id=org_id,
        )
        self.db.add(base)
        await self.db.flush()
        # Not a security-sensitive action, but a base is the thing every dataset's
        # reach hangs off, so "who made this shared" has an answer.
        await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=org_id,
            action="dataset.base_created",
            target_type="knowledge_base",
            target_id=str(base.id),
            details={"name": base.name, "scope": base.scope, "kind": "dataset"},
        )
        return base

    # -- datasets -----------------------------------------------------------

    def read(self, dataset: Dataset, fields: Any, metrics: Any) -> DatasetRead:
        return DatasetRead(
            id=dataset.id,
            knowledge_base_id=dataset.knowledge_base_id,
            organization_id=dataset.organization_id,
            name=dataset.name,
            sheet_name=dataset.sheet_name,
            source_filename=dataset.source_filename,
            primary_key_fields=list(dataset.primary_key_fields),
            current_version_no=dataset.current_version_no,
            model_version=dataset.model_version,
            storage_mode=dataset.storage_mode,  # type: ignore[arg-type]
            row_count=dataset.row_count,
            revision=dataset.revision,
            created_at=dataset.created_at,
            updated_at=dataset.updated_at,
            last_import_at=dataset.last_import_at,
            fields=[
                {
                    "name": field.name,
                    "display_name": field.display_name,
                    "data_type": field.data_type,
                    "ordinal": field.ordinal,
                    "is_nullable": field.is_nullable,
                    "is_primary_key": field.is_primary_key,
                    "synonyms": list(field.synonyms),
                    "format_hint": field.format_hint,
                    "description": field.description,
                }
                for field in fields
            ],
            metrics=[
                {
                    "name": metric.name,
                    "display_name": metric.display_name,
                    "agg": metric.agg,
                    "field_name": metric.field_name,
                    "format_hint": metric.format_hint,
                    "description": metric.description,
                }
                for metric in metrics
            ],
        )

    async def list_datasets(self, kb_id: uuid.UUID, ctx: Any) -> list[DatasetRead]:
        kb = await self.kb(kb_id, ctx)
        datasets = await repo.get_dataset_by_kb(self.db, kb.id)
        result: list[DatasetRead] = []
        for dataset in datasets:
            if dataset.archived_at is not None:
                continue
            result.append(
                self.read(
                    dataset,
                    await repo.list_fields(self.db, dataset.id),
                    await repo.list_metrics(self.db, dataset.id),
                )
            )
        return result

    async def get_dataset(self, dataset_id: uuid.UUID, ctx: Any) -> DatasetRead:
        dataset = await self.dataset(dataset_id, ctx)
        return self.read(
            dataset,
            await repo.list_fields(self.db, dataset.id),
            await repo.list_metrics(self.db, dataset.id),
        )

    async def create(self, kb_id: uuid.UUID, data: DatasetCreate, ctx: Any) -> DatasetRead:
        _require_enabled()
        kb = await self.kb(kb_id, ctx, write=True)
        await quota_service.refuse_a_full_base(self.db, kb.id)
        dataset = await model_service.create_dataset(
            self.db,
            knowledge_base_id=kb.id,
            organization_id=kb.organization_id or ctx.organization_id,
            name=data.name,
            fields=data.fields,
            metrics=data.metrics,
            sheet_name=data.sheet_name,
            source_filename=data.source_filename,
            note=data.note,
            created_by=ctx.user_id,
        )
        await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=dataset.organization_id,
            action="dataset.created",
            target_type="dataset",
            target_id=str(dataset.id),
            details={
                "name": dataset.name,
                "knowledge_base_id": str(kb.id),
                "fields": len(data.fields),
                "primary_key": list(dataset.primary_key_fields),
            },
        )
        return self.read(
            dataset,
            await repo.list_fields(self.db, dataset.id),
            await repo.list_metrics(self.db, dataset.id),
        )

    async def rename(self, dataset_id: uuid.UUID, data: DatasetUpdate, ctx: Any) -> DatasetRead:
        dataset = await self.dataset(dataset_id, ctx, write=True)
        if data.name and data.name != dataset.name:
            existing = await repo.find_dataset(self.db, dataset.knowledge_base_id, data.name)
            if existing is not None:
                raise BadRequestError(message=f"A dataset named `{data.name}` already exists")
            dataset.name = data.name
        await self.db.flush()
        # The UPDATE carries `updated_at = now()` server-side, which leaves the
        # attribute expired; reading it back is IO and must happen in an awaited
        # call. Without this the response the section renders raised
        # `MissingGreenlet` instead of answering (#T29).
        await self.db.refresh(dataset)
        return self.read(
            dataset,
            await repo.list_fields(self.db, dataset.id),
            await repo.list_metrics(self.db, dataset.id),
        )

    async def update_model(
        self, dataset_id: uuid.UUID, data: DatasetFieldChange, ctx: Any
    ) -> DatasetRead:
        dataset = await self.dataset(dataset_id, ctx, write=True)
        await model_service.update_model(
            self.db,
            dataset,
            fields=data.fields,
            metrics=data.metrics,
            note=data.note,
            actor_user_id=ctx.user_id,
        )
        await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=dataset.organization_id,
            action="dataset.model_updated",
            target_type="dataset",
            target_id=str(dataset.id),
            details={
                "model_version": dataset.model_version,
                "fields": [field.name for field in data.fields],
                "primary_key": list(dataset.primary_key_fields),
                "note": data.note,
            },
        )
        return self.read(
            dataset,
            await repo.list_fields(self.db, dataset.id),
            await repo.list_metrics(self.db, dataset.id),
        )

    # -- rows and versions --------------------------------------------------

    async def list_rows(
        self,
        dataset_id: uuid.UUID,
        ctx: Any,
        *,
        version_no: int | None,
        search: str | None,
        skip: int,
        limit: int,
    ) -> DatasetRowList:
        dataset = await self.dataset(dataset_id, ctx)
        if version_no is not None and version_no > dataset.current_version_no:
            raise BadRequestError(message="That version has not been published yet")
        rows, total = await repo.page_rows(
            self.db,
            partition_key=dataset.storage_partition_key,
            dataset_id=dataset.id,
            version_no=version_no,
            search=search,
            skip=skip,
            limit=limit,
        )
        fields = await repo.list_fields(self.db, dataset.id)
        return DatasetRowList(
            items=[
                DatasetRowRead(
                    record_key=row["record_key"],
                    record_version=row["record_version"],
                    valid_from=row["valid_from"],
                    valid_to=row["valid_to"],
                    is_deleted=row["is_deleted"],
                    values=row["values"],
                )
                for row in rows
            ],
            total=total,
            version_no=version_no or dataset.current_version_no,
            columns=[
                {
                    "name": field.name,
                    "display_name": field.display_name,
                    "data_type": field.data_type,
                    "ordinal": field.ordinal,
                    "is_nullable": field.is_nullable,
                    "is_primary_key": field.is_primary_key,
                    "synonyms": list(field.synonyms),
                    "format_hint": field.format_hint,
                    "description": field.description,
                }
                for field in fields
            ],
        )

    async def row_snapshot(
        self, dataset_id: uuid.UUID, ctx: Any, *, record_key: str, version_no: int
    ) -> dict[str, Any]:
        dataset = await self.dataset(dataset_id, ctx)
        snapshot = await maintenance_service.snapshot_at(
            self.db, dataset, record_key_value=record_key, version_no=version_no
        )
        if snapshot is None:
            raise NotFoundError(message="No such row at that version")
        return snapshot

    async def versions(
        self, dataset_id: uuid.UUID, ctx: Any, *, skip: int, limit: int
    ) -> dict[str, Any]:
        dataset = await self.dataset(dataset_id, ctx)
        versions, total = await repo.list_versions(self.db, dataset.id, skip=skip, limit=limit)
        pinned = await repo.pinned_versions(self.db, dataset.id)
        return {
            "items": [
                {
                    "id": version.id,
                    "dataset_id": version.dataset_id,
                    "version_no": version.version_no,
                    "kind": version.kind,
                    "source_filename": version.source_filename,
                    "sheet_name": version.sheet_name,
                    "rows_added": version.rows_added,
                    "rows_changed": version.rows_changed,
                    "rows_removed": version.rows_removed,
                    "rows_unchanged": version.rows_unchanged,
                    "note": version.note,
                    "published_at": version.published_at,
                    "published_by": version.published_by,
                    "stats": version.stats,
                    "pinned": version.version_no in pinned,
                    "current": version.version_no == dataset.current_version_no,
                }
                for version in versions
            ],
            "total": total,
        }

    async def version_snapshot(
        self, dataset_id: uuid.UUID, ctx: Any, version_no: int
    ) -> dict[str, Any]:
        dataset = await self.dataset(dataset_id, ctx)
        return await model_service.snapshot(self.db, dataset, version_no)

    async def compare(
        self,
        dataset_id: uuid.UUID,
        ctx: Any,
        *,
        from_version: int,
        to_version: int,
        limit: int,
    ) -> DatasetCompareResult:
        dataset = await self.dataset(dataset_id, ctx)
        return await compare_service.compare(
            self.db,
            dataset,
            from_version=from_version,
            to_version=to_version,
            limit=limit,
        )

    # -- query --------------------------------------------------------------

    async def _query_context(
        self, dataset: Dataset, *, version_no: int | None, ctx: Any
    ) -> QueryContext:
        fields = await repo.list_fields(self.db, dataset.id)
        metrics = await repo.list_metrics(self.db, dataset.id)
        targets: dict[str, RelationTarget] = {}
        for relation in await repo.list_relations(self.db, dataset.id):
            if relation.status == "disabled":
                continue
            target = await repo.get_dataset(self.db, relation.target_dataset_id)
            if target is None or target.archived_at is not None:
                continue
            target_fields = await repo.list_fields(self.db, target.id)
            targets[relation.name] = RelationTarget(
                relation=relation,
                dataset=target,
                fields={field.name: field for field in target_fields},
            )
        return QueryContext(
            dataset=dataset,
            fields={field.name: field for field in fields},
            metrics={metric.name: metric for metric in metrics},
            relations=targets,
            version_no=version_no or dataset.current_version_no,
        )

    async def query(
        self,
        dataset_id: uuid.UUID,
        ctx: Any,
        request: DatasetQuery,
        *,
        citation: Citation | None = None,
    ) -> DatasetQueryResult:
        """Answer a question, and - when a `citation` is given - protect what it read.

        The pin is written by the service rather than by the caller because this is
        the only place that knows *which* datasets a compiled statement touched: a
        join and a subquery each read a second dataset, and an answer computed from
        one is exactly the answer that has to survive retention.
        """
        dataset = await self.dataset(dataset_id, ctx)
        context = await self._query_context(dataset, version_no=request.as_of_version, ctx=ctx)
        started = time.perf_counter()
        try:
            compiled = compile_query(context, request)
            rows, truncated = await run_query(self.db, compiled)
        except BadRequestError as exc:
            metrics_service.query_rejected(reason=exc.message[:120])
            raise
        metrics_service.query_finished(
            rows=len(rows),
            joins=len(request.joins) + len(request.subqueries),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        if citation is not None:
            await self._pin_citation(context, compiled.version_vector, citation)
        return DatasetQueryResult(
            columns=[
                QueryColumn(name=column.name, data_type=column.data_type)
                for column in compiled.columns
            ],
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            version_vector=compiled.version_vector,
            sql=compiled.sql,
            params={key: str(value) for key, value in compiled.params.items()},
            warnings=compiled.warnings,
        )

    async def _pin_citation(
        self, context: QueryContext, versions: dict[str, int], citation: Citation
    ) -> None:
        """Keep the versions this answer was computed from.

        One pin per (dataset, version), and the *first* citation is the one it
        records: retention only needs to know the version is still quoted, and a
        row per conversation would grow with the conversation rather than with the
        data. A version that is already pinned is left alone, which is also what
        makes a retried tool call idempotent.
        """
        pins: list[dict[str, Any]] = []
        for dataset in [
            context.dataset,
            *(target.dataset for target in context.relations.values()),
        ]:
            version = versions.get(dataset.name)
            if version is None:
                continue
            if version in await repo.pinned_versions(self.db, dataset.id):
                continue
            pins.append(
                {
                    "dataset_id": dataset.id,
                    "version_no": version,
                    "tool_call_id": citation.tool_call_id,
                    "conversation_id": citation.conversation_id,
                    "message_id": citation.message_id,
                }
            )
        await repo.pin_versions(self.db, pins)

    async def resolve_by_name(self, kb_id: uuid.UUID, name: str, ctx: Any) -> Dataset:
        kb = await self.kb(kb_id, ctx)
        dataset = await repo.find_dataset(self.db, kb.id, name)
        if dataset is None:
            try:
                dataset = await self.dataset(uuid.UUID(name), ctx)
            except ValueError, NotFoundError:
                dataset = None
        if dataset is None or dataset.knowledge_base_id != kb.id:
            raise NotFoundError(message=f"No dataset named `{name}` in this knowledge base")
        return dataset

    # -- maintenance --------------------------------------------------------

    async def update_row(
        self,
        dataset_id: uuid.UUID,
        ctx: Any,
        *,
        record_key: str,
        values: dict[str, Any],
        reason: str,
        expected_version: int | None,
    ) -> dict[str, Any]:
        dataset = await self.dataset(dataset_id, ctx, write=True)
        version_no = await maintenance_service.update_row(
            self.db,
            dataset,
            record_key_value=record_key,
            values=values,
            reason=reason,
            actor_user_id=ctx.user_id,
            expected_version=expected_version,
        )
        await self._audit_row(
            ctx,
            dataset,
            action="dataset.row_updated",
            record_key=record_key,
            version_no=version_no,
            reason=reason,
        )
        return {"version_no": version_no, "record_key": record_key}

    async def insert_row(
        self,
        dataset_id: uuid.UUID,
        ctx: Any,
        *,
        values: dict[str, Any],
        reason: str,
        expected_version: int | None,
    ) -> dict[str, Any]:
        dataset = await self.dataset(dataset_id, ctx, write=True)
        version_no = await maintenance_service.insert_row(
            self.db,
            dataset,
            values=values,
            reason=reason,
            actor_user_id=ctx.user_id,
            expected_version=expected_version,
        )
        await self._audit_row(
            ctx,
            dataset,
            action="dataset.row_inserted",
            # The key the row was inserted under, as the model names it: an insert
            # has no `record_key` argument to echo, and the caller is the one that
            # supplied the values the key is made of.
            record_key="/".join(str(values.get(field, "")) for field in dataset.primary_key_fields)
            or "(new row)",
            version_no=version_no,
            reason=reason,
        )
        return {"version_no": version_no}

    async def delete_row(
        self,
        dataset_id: uuid.UUID,
        ctx: Any,
        *,
        record_key: str,
        reason: str,
        expected_version: int | None,
    ) -> dict[str, Any]:
        dataset = await self.dataset(dataset_id, ctx, write=True)
        version_no = await maintenance_service.delete_row(
            self.db,
            dataset,
            record_key_value=record_key,
            reason=reason,
            actor_user_id=ctx.user_id,
            expected_version=expected_version,
        )
        await self._audit_row(
            ctx,
            dataset,
            action="dataset.row_deleted",
            record_key=record_key,
            version_no=version_no,
            reason=reason,
        )
        return {"version_no": version_no, "record_key": record_key}

    async def restore_row(
        self,
        dataset_id: uuid.UUID,
        ctx: Any,
        *,
        record_key: str,
        reason: str,
        expected_version: int | None,
    ) -> dict[str, Any]:
        dataset = await self.dataset(dataset_id, ctx, write=True)
        version_no = await maintenance_service.restore_row(
            self.db,
            dataset,
            record_key_value=record_key,
            reason=reason,
            actor_user_id=ctx.user_id,
            expected_version=expected_version,
        )
        await self._audit_row(
            ctx,
            dataset,
            action="dataset.row_restored",
            record_key=record_key,
            version_no=version_no,
            reason=reason,
        )
        return {"version_no": version_no, "record_key": record_key}

    async def operations(
        self, dataset_id: uuid.UUID, ctx: Any, *, skip: int, limit: int
    ) -> dict[str, Any]:
        dataset = await self.dataset(dataset_id, ctx)
        operations, total = await repo.list_operations(self.db, dataset.id, skip=skip, limit=limit)
        return {
            "items": [
                {
                    "id": operation.id,
                    "version_no": operation.version_no,
                    "operation": operation.operation,
                    "record_key": operation.record_key,
                    "before": operation.before,
                    "after": operation.after,
                    "reason": operation.reason,
                    "actor_user_id": operation.actor_user_id,
                    "created_at": operation.created_at,
                }
                for operation in operations
            ],
            "total": total,
        }

    # -- relations ----------------------------------------------------------

    async def relations(self, dataset_id: uuid.UUID, ctx: Any) -> list[dict[str, Any]]:
        dataset = await self.dataset(dataset_id, ctx)
        items: list[dict[str, Any]] = []
        for relation in await repo.list_relations(self.db, dataset.id):
            target = await repo.get_dataset(self.db, relation.target_dataset_id)
            items.append(
                {
                    "id": relation.id,
                    "dataset_id": relation.dataset_id,
                    "target_dataset_id": relation.target_dataset_id,
                    "target_dataset_name": target.name if target else None,
                    "name": relation.name,
                    "cardinality": relation.cardinality,
                    "join_type": relation.join_type,
                    "local_fields": list(relation.local_fields),
                    "foreign_fields": list(relation.foreign_fields),
                    "status": relation.status,
                    "confidence": float(relation.confidence)
                    if relation.confidence is not None
                    else None,
                    "description": relation.description,
                    "created_at": relation.created_at,
                    "updated_at": relation.updated_at,
                }
            )
        return items

    async def create_relation(
        self, dataset_id: uuid.UUID, ctx: Any, data: DatasetRelationInput
    ) -> tuple[dict[str, Any], list[str]]:
        dataset = await self.dataset(dataset_id, ctx, write=True)
        relation, warnings = await relations_service.create(self.db, dataset, data)
        await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=dataset.organization_id,
            action="dataset.relation_created",
            target_type="dataset_relation",
            target_id=str(relation.id),
            details={
                "dataset_id": str(dataset.id),
                "target_dataset_id": str(relation.target_dataset_id),
                "name": relation.name,
                "cardinality": relation.cardinality,
                "local_fields": list(relation.local_fields),
                "foreign_fields": list(relation.foreign_fields),
            },
        )
        return (
            {
                "id": relation.id,
                "dataset_id": relation.dataset_id,
                "target_dataset_id": relation.target_dataset_id,
                "target_dataset_name": data.target_dataset_id and None,
                "name": relation.name,
                "cardinality": relation.cardinality,
                "join_type": relation.join_type,
                "local_fields": list(relation.local_fields),
                "foreign_fields": list(relation.foreign_fields),
                "status": relation.status,
                "confidence": float(relation.confidence)
                if relation.confidence is not None
                else None,
                "description": relation.description,
                "created_at": relation.created_at,
                "updated_at": relation.updated_at,
            },
            warnings,
        )

    async def delete_relation(
        self, dataset_id: uuid.UUID, relation_id: uuid.UUID, ctx: Any
    ) -> None:
        dataset = await self.dataset(dataset_id, ctx, write=True)
        relation = await repo.get_relation_by_id(self.db, relation_id)
        if relation is None or relation.dataset_id != dataset.id:
            raise NotFoundError(message="No such relation")
        await repo.delete_relation(self.db, relation.id)
        await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=dataset.organization_id,
            action="dataset.relation_deleted",
            target_type="dataset_relation",
            target_id=str(relation.id),
            details={"dataset_id": str(dataset.id), "name": relation.name},
        )

    async def relation_candidates(self, dataset_id: uuid.UUID, ctx: Any) -> list[dict[str, Any]]:
        dataset = await self.dataset(dataset_id, ctx)
        return await relations_service.infer_candidates(self.db, dataset)

    # -- imports ------------------------------------------------------------

    async def preview_import(
        self, kb_id: uuid.UUID, ctx: Any, *, filename: str, content: bytes
    ) -> Any:
        _require_enabled()
        kb = await self.kb(kb_id, ctx, write=True)
        return await imports_service.preview(
            self.db,
            knowledge_base_id=kb.id,
            organization_id=kb.organization_id or ctx.organization_id,
            filename=filename,
            content=content,
            actor_user_id=ctx.user_id,
        )

    async def commit_import(
        self, kb_id: uuid.UUID, import_id: uuid.UUID, ctx: Any, payload: DatasetImportCommit
    ) -> dict[str, Any]:
        """Publish here and now, for a caller that asked to wait.

        The wizard's small-workbook path and the worker both come through here;
        `queue_import` is what a large one uses, and the publishing itself is
        the same function either way.
        """
        _require_enabled()
        kb = await self.kb(kb_id, ctx, write=True)
        record = await repo.get_import(self.db, import_id)
        if record is None or record.knowledge_base_id != kb.id:
            raise NotFoundError(message="No such import")
        result = await imports_service.commit(
            self.db,
            record=record,
            knowledge_base_id=kb.id,
            organization_id=kb.organization_id or ctx.organization_id,
            payload=payload,
            actor_user_id=ctx.user_id,
        )
        await imports_service.record_commit(
            self.db,
            record=record,
            knowledge_base_id=kb.id,
            organization_id=kb.organization_id or ctx.organization_id,
            actor_user_id=ctx.subject_id,
            result=result,
        )
        return result

    async def queue_import(
        self, kb_id: uuid.UUID, import_id: uuid.UUID, ctx: Any, payload: DatasetImportCommit
    ) -> dict[str, Any]:
        """Accept a commit and let a worker publish it.

        Answers with the queued row rather than the result: the workbook is the
        thing that takes minutes, and an HTTP connection is not the place to
        hold one. The audit is written by the worker, when there is something to
        report.
        """
        _require_enabled()
        kb = await self.kb(kb_id, ctx, write=True)
        record = await repo.get_import(self.db, import_id)
        if record is None or record.knowledge_base_id != kb.id:
            raise NotFoundError(message="No such import")
        return await imports_service.accept(
            self.db,
            record=record,
            knowledge_base_id=kb.id,
            organization_id=kb.organization_id or ctx.organization_id,
            payload=payload,
            actor_user_id=ctx.user_id,
        )

    async def save_import_draft(
        self, kb_id: uuid.UUID, import_id: uuid.UUID, ctx: Any, draft: DatasetImportDraft
    ) -> dict[str, Any]:
        """Keep the wizard's decisions against the import, so it can be resumed.

        Nothing is published and no rows are written: this is the draft half of
        the flow, and it is what "断点续" means - the person closes the dialog
        and comes back to the step they left.
        """
        _require_enabled()
        kb = await self.kb(kb_id, ctx, write=True)
        record = await repo.get_import(self.db, import_id)
        if record is None or record.knowledge_base_id != kb.id:
            raise NotFoundError(message="No such import")
        return await imports_service.save_draft(self.db, record=record, draft=draft)

    async def import_status(
        self, kb_id: uuid.UUID, import_id: uuid.UUID, ctx: Any
    ) -> dict[str, Any]:
        """One import's progress, and where the wizard left off.

        The same answer serves the poller during a publish and the dialog being
        reopened a day later: `progress` is how far the worker got, `plan` is
        the parsed workbook and the decisions saved against it.
        """
        _require_enabled()
        kb = await self.kb(kb_id, ctx)
        record = await repo.get_import(self.db, import_id)
        if record is None or record.knowledge_base_id != kb.id:
            raise NotFoundError(message="No such import")
        return {
            "id": record.id,
            "knowledge_base_id": record.knowledge_base_id,
            "status": record.status,
            "filename": record.filename,
            "sheet_count": record.sheet_count,
            "progress": imports_service.progress_of(record),
            "sheets": (record.summary or {}).get("sheets") or [],
            "plan": imports_service.plan_of(record),
            "error": record.error,
            "committed_at": record.committed_at,
        }

    async def imports(self, kb_id: uuid.UUID, ctx: Any, *, skip: int, limit: int) -> dict[str, Any]:
        kb = await self.kb(kb_id, ctx)
        records, total = await repo.list_imports(self.db, kb.id, skip=skip, limit=limit)
        return {
            "items": [
                {
                    "id": record.id,
                    "knowledge_base_id": record.knowledge_base_id,
                    "status": record.status,
                    "filename": record.filename,
                    "file_size": record.file_size,
                    "sheet_count": record.sheet_count,
                    "summary": record.summary,
                    "error": record.error,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "committed_at": record.committed_at,
                    "sheets": (record.plan or {}).get("sheets", []),
                }
                for record in records
            ],
            "total": total,
        }

    # -- pins ---------------------------------------------------------------

    async def pin(
        self,
        dataset_id: uuid.UUID,
        ctx: Any,
        *,
        version_no: int,
        tool_call_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        message_id: uuid.UUID | None = None,
    ) -> None:
        """Protect one version from retention, on behalf of `ctx`.

        The dataset is resolved through the caller's own access, not pinned by
        id: a pin is a write on somebody's history, and a method that takes only
        an id is one a route could forget to guard (T32).
        """
        dataset = await self.dataset(dataset_id, ctx, write=True)
        await repo.pin_versions(
            self.db,
            [
                {
                    "dataset_id": dataset.id,
                    "version_no": version_no,
                    "tool_call_id": tool_call_id,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                }
            ],
        )

    async def republish(
        self,
        dataset: Dataset,
        rows: list[dict[str, Any]],
        *,
        key_fields: list[str],
        actor_user_id: uuid.UUID | None,
        note: str | None = None,
    ) -> dict[str, Any]:
        result = await publish(
            self.db,
            dataset,
            rows,
            key_fields=key_fields,
            kind="import",
            note=note,
            actor_user_id=actor_user_id,
        )
        return {
            "version_no": result.version_no,
            "published": result.published,
            "rows_added": result.rows_added,
            "rows_changed": result.rows_changed,
            "rows_unchanged": result.rows_unchanged,
            "warnings": result.warnings,
        }

    async def archive(self, dataset_id: uuid.UUID, ctx: Any) -> None:
        dataset = await self.dataset(dataset_id, ctx, write=True)
        await repo.archive_dataset(self.db, dataset.id, datetime.now(UTC))
        # The rows and versions are kept: archiving takes the dataset out of every
        # new question, and the entry is what lets somebody later say when that
        # happened and who decided it.
        await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=dataset.organization_id,
            action="dataset.archived",
            target_type="dataset",
            target_id=str(dataset.id),
            details={
                "name": dataset.name,
                "current_version_no": dataset.current_version_no,
                "row_count": dataset.row_count,
            },
        )
