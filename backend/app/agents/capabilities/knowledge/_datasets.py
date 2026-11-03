"""The four tools an agent uses to read datasets.

They are the same service the console calls, with the same access rules: what an
agent may read is the knowledge bases it is bound to, and a dataset outside them
is `NOT_FOUND` here exactly as it is there. The tools therefore take a dataset
*name*, never an id, and resolve it inside the bound bases.

Every answer names the versions it read. That is what lets a later question be
answered against the same numbers after the data has moved on, and what lets the
conversation say "this was version 7" rather than "this was last week".
"""

import json
import uuid
from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext

from app.agents.deps import AgentDeps
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.permissions import AuthContext
from app.db.models.knowledge_base import KBType
from app.db.session import get_db_context
from app.schemas.kb_dataset import DatasetQuery, JoinSpec, OrderSpec, SelectItem, WindowSpec
from app.services.access import acting_member
from app.services.kb_dataset import Citation, DatasetService

_NO_PERSON = "This run acts for nobody, so it cannot read an organization's data."
_NO_DATASETS = (
    "No dataset knowledge base is bound to this agent. Bind an Excel dataset base to "
    "answer questions about its numbers."
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _citation(ctx: RunContext[AgentDeps]) -> Citation:
    """What a pin records about the tool call that caused it.

    `tool_call_id` is a string on the context and a uuid in the column, so a value
    that is not one is dropped rather than failing the answer: the pin's job is to
    keep the version alive, and an untraceable pin still does that.
    """
    raw = getattr(ctx, "tool_call_id", None)
    token: uuid.UUID | None = None
    if raw:
        try:
            token = uuid.UUID(str(raw))
        except ValueError:
            token = None
    return Citation(tool_call_id=token, conversation_id=ctx.deps.conversation_id)


async def _service(deps: AgentDeps, db: Any) -> tuple[DatasetService, AuthContext, list[Any]]:
    """The service, the person it acts for, and the dataset bases this agent may read."""
    if deps.run_person is None or deps.organization_id is None:
        raise _NoPerson
    person = await acting_member(db, deps.organization_id, str(deps.run_person))
    service = DatasetService(db)
    kb_ids = {
        scope.knowledge_base_id
        for scope in deps.kb_vector_scopes.values()
        if scope.knowledge_base_id is not None
    }
    bases = []
    for kb_id in kb_ids:
        try:
            kb = await service.kb(kb_id, person)
        except AppException:
            continue
        if kb.kb_type == KBType.DATASET.value:
            bases.append(kb)
    return service, person, bases


class _NoPerson(Exception):
    pass


async def _find(service: DatasetService, person: AuthContext, bases: list[Any], name: str):
    """The dataset called `name`, its read model, and the base it was found in.

    The base comes back because the console opens a dataset at its base's page,
    and a citation that names only the dataset has nowhere to point (#T22).
    """
    for kb in bases:
        try:
            dataset = await service.resolve_by_name(kb.id, name, person)
        except AppException:
            continue
        base = service.read(
            dataset,
            await _fields(service, dataset),
            await _metrics(service, dataset),
        )
        return dataset, base, kb
    raise AppException(message=f"No dataset named `{name}` is bound to this agent")


async def _fields(service: DatasetService, dataset: Any) -> Any:
    from app.repositories import kb_dataset as repo

    return await repo.list_fields(service.db, dataset.id)


async def _metrics(service: DatasetService, dataset: Any) -> Any:
    from app.repositories import kb_dataset as repo

    return await repo.list_metrics(service.db, dataset.id)


def build_dataset_tools() -> list[Callable[..., Any]]:
    """The four tools, or none when the deployment has datasets switched off.

    Empty rather than refusing inside each tool: an agent that cannot ask does not
    offer the capability, and a model handed four tools that all answer "this
    deployment has datasets turned off" spends a turn learning what the toolset
    could have told it by not being there.
    """
    if not settings.KB_DATASETS_ENABLED:
        return []

    async def list_datasets(ctx: RunContext[AgentDeps]) -> str:
        """List the Excel datasets this agent can query, with their size and version.

        Call this first when a question mentions numbers, tables, sales, orders,
        headcount or anything else that sounds like rows rather than documents.
        A dataset name from here is what the other dataset tools expect.
        """
        try:
            async with get_db_context() as db:
                service, person, bases = await _service(ctx.deps, db)
                if not bases:
                    return _NO_DATASETS
                payload = []
                for kb in bases:
                    for item in await service.list_datasets(kb.id, person):
                        payload.append(
                            {
                                # The console links a citation to its dataset by
                                # id: a name is what the model writes, and it is
                                # not a resource locator (#T22).
                                "id": str(item.id),
                                "kb_id": str(kb.id),
                                "name": item.name,
                                "description": kb.description,
                                "rows": item.row_count,
                                "version": item.current_version_no,
                                "model_version": item.model_version,
                                "primary_key": item.primary_key_fields,
                                "fields": [field.name for field in item.fields],
                            }
                        )
                return _dump(payload)
        except _NoPerson:
            return _NO_PERSON

    async def describe_dataset(ctx: RunContext[AgentDeps], dataset: str) -> str:
        """Show one dataset's fields, types, metrics, versions and registered joins.

        Do this before writing a query: field names must match exactly, metrics
        are the only aggregates that may be selected by name, and a join is only
        allowed along a relation listed here.
        """
        try:
            async with get_db_context() as db:
                service, person, bases = await _service(ctx.deps, db)
                if not bases:
                    return _NO_DATASETS
                found, base, kb = await _find(service, person, bases, dataset)
                relations = await service.relations(found.id, person)
                versions = await service.versions(found.id, person, skip=0, limit=10)
                return _dump(
                    {
                        "dataset_id": str(base.id),
                        "kb_id": str(kb.id),
                        "name": base.name,
                        "rows": base.row_count,
                        "current_version": base.current_version_no,
                        "model_version": base.model_version,
                        "primary_key": base.primary_key_fields,
                        "fields": [
                            {
                                "name": field.name,
                                "display": field.display_name,
                                "type": field.data_type,
                                "synonyms": field.synonyms,
                                "description": field.description,
                            }
                            for field in base.fields
                        ],
                        "metrics": [
                            {
                                "name": metric.name,
                                "agg": metric.agg,
                                "field": metric.field_name,
                                "description": metric.description,
                            }
                            for metric in base.metrics
                        ],
                        "relations": [
                            {
                                "name": relation["name"],
                                "to": relation["target_dataset_name"],
                                "cardinality": relation["cardinality"],
                                "on": dict(
                                    zip(
                                        relation["local_fields"],
                                        relation["foreign_fields"],
                                        strict=False,
                                    )
                                ),
                                "status": relation["status"],
                            }
                            for relation in relations
                        ],
                        "recent_versions": [
                            {
                                "version": version["version_no"],
                                "kind": version["kind"],
                                "added": version["rows_added"],
                                "changed": version["rows_changed"],
                                "removed": version["rows_removed"],
                                "at": version["published_at"],
                            }
                            for version in versions["items"]
                        ],
                    }
                )
        except _NoPerson:
            return _NO_PERSON

    async def query_dataset(
        ctx: RunContext[AgentDeps],
        dataset: str,
        fields: list[str] | None = None,
        metrics: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        group_by: list[str] | None = None,
        order_by: list[str] | None = None,
        joins: list[str] | None = None,
        windows: list[dict[str, Any]] | None = None,
        as_of_version: int | None = None,
        limit: int = 100,
    ) -> str:
        """Ask a dataset a question and get rows back.

        Write it the way you would describe a report: which columns, which
        rows, what to group by, what to total.

        Args:
            dataset: A dataset name from `list_datasets`.
            fields: Column names to return, e.g. ["region", "orders.amount"].
                A name prefixed with a joined relation (see `joins`) reads that
                dataset's column.
            metrics: Metric names defined on the dataset, e.g. ["revenue"].
                Use these rather than inventing an aggregate.
            filters: A predicate or group of them, e.g.
                {"field": "region", "op": "eq", "value": "华东"} or
                {"and": [{"field": "amount", "op": "gte", "value": 100},
                         {"field": "month", "op": "in", "values": ["2026-01","2026-02"]}]}.
                Operators: eq, ne, gt, gte, lt, lte, in, not_in, like, is_null, between.
            group_by: Fields or metric names to group by. When metrics are
                selected, every non-metric field must be grouped.
            order_by: Result columns to sort by, descending by default; prefix
                with `-` for ascending.
            joins: Relation names to join along, e.g. ["to_customers"].
            windows: Calculations over the result, e.g.
                [{"macro": "mom", "value": "revenue", "alias": "growth",
                  "partition_by": ["region"], "order_by": "month"}].
                Macros: mom, yoy, share, cum, rank, topn.
            as_of_version: Read the data as it stood at this version instead of
                the current one. Use it to keep an answer consistent with an
                earlier one.
            limit: Rows at most (1-1000).
        """
        try:
            async with get_db_context() as db:
                service, person, bases = await _service(ctx.deps, db)
                if not bases:
                    return _NO_DATASETS
                found, _base, kb = await _find(service, person, bases, dataset)
                request = DatasetQuery(
                    dataset=found.name,
                    fields=[SelectItem(field=name) for name in (fields or [])]
                    + [SelectItem(metric=name) for name in (metrics or [])],
                    filters=filters,
                    group_by=list(group_by or []),
                    joins=[JoinSpec(relation=name) for name in (joins or [])],
                    order_by=[
                        OrderSpec(
                            field=name.lstrip("-"),
                            descending=not name.startswith("-"),
                        )
                        for name in (order_by or [])
                    ],
                    windows=[WindowSpec.model_validate(window) for window in (windows or [])],
                    as_of_version=as_of_version,
                    limit=limit,
                )
                result = await service.query(found.id, person, request, citation=_citation(ctx))
                return _dump(
                    {
                        "dataset_id": str(found.id),
                        "kb_id": str(kb.id),
                        "dataset": found.name,
                        "version_vector": result.version_vector,
                        "columns": [column.name for column in result.columns],
                        "rows": result.rows,
                        "row_count": result.row_count,
                        "truncated": result.truncated,
                        "warnings": result.warnings,
                    }
                )
        except _NoPerson:
            return _NO_PERSON
        except AppException as exc:
            return f"The dataset refused that query: {exc.message}"

    async def compare_dataset_versions(
        ctx: RunContext[AgentDeps],
        dataset: str,
        from_version: int,
        to_version: int,
    ) -> str:
        """Compare two versions: what was added, changed, removed or no longer listed.

        Use it when a person asks what changed between two imports, or why a
        number moved. `removed` means somebody deleted the record through
        maintenance; `missing` means it is simply absent from that version.
        """
        try:
            async with get_db_context() as db:
                service, person, bases = await _service(ctx.deps, db)
                if not bases:
                    return _NO_DATASETS
                found, _base, kb = await _find(service, person, bases, dataset)
                result = await service.compare(
                    found.id,
                    person,
                    from_version=from_version,
                    to_version=to_version,
                    limit=100,
                )
                return _dump(
                    {
                        "dataset_id": str(found.id),
                        "kb_id": str(kb.id),
                        "dataset": found.name,
                        "from_version": result.from_version,
                        "to_version": result.to_version,
                        "added": result.added,
                        "changed": result.changed,
                        "removed": result.removed,
                        "missing": result.missing,
                        "unchanged": result.unchanged,
                        "rows": [row.model_dump() for row in result.rows],
                        "truncated": result.truncated,
                    }
                )
        except _NoPerson:
            return _NO_PERSON
        except AppException as exc:
            return f"The comparison was refused: {exc.message}"

    return [list_datasets, describe_dataset, query_dataset, compare_dataset_versions]
