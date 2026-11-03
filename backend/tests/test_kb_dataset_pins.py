"""Pinning the versions an answer was computed from (plan T16).

A citation says "this number came from v3 of 订单明细". Retention cannot remove v3
without breaking that sentence, so the query that produced the answer registers the
versions it read. Three properties matter, and each has a cost when it is wrong:

- every dataset the statement read is pinned, not just the root one - a joined
  lookup is as much a source as the table the question named, and pinning only the
  root leaves the join free to disappear;
- a described version is pinned once, so a retried tool call does not grow the
  table and a conversation that quotes the same version twice adds nothing;
- no citation, no pin - a console preview or a schema probe is not an answer.
"""

import uuid
from typing import Any

import pytest

from app.db.models.kb_dataset import Dataset, DatasetField, DatasetRelation
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import DatasetQuery
from app.services.kb_dataset import Citation, DatasetService
from app.services.kb_dataset.query import QueryContext, RelationTarget

pytestmark = pytest.mark.anyio


def _dataset(name: str, version: int) -> Dataset:
    dataset = Dataset()
    dataset.id = uuid.uuid4()
    dataset.name = name
    dataset.storage_partition_key = uuid.uuid4()
    dataset.current_version_no = version
    return dataset


def _field(name: str, data_type: str) -> DatasetField:
    field = DatasetField()
    field.name = name
    field.data_type = data_type
    return field


class Base:
    """The two datasets a joined question reads, and what the join is called."""

    def __init__(self) -> None:
        self.orders = _dataset("订单", version=5)
        self.customers = _dataset("客户", version=2)
        relation = DatasetRelation()
        relation.name = "客户表"
        relation.local_fields = ["客户编号"]
        relation.foreign_fields = ["客户编号"]
        relation.join_type = "left"
        relation.target_dataset_id = self.customers.id
        relation.status = "confirmed"
        self.context = QueryContext(
            dataset=self.orders,
            fields={
                "订单编号": _field("订单编号", "text"),
                "客户编号": _field("客户编号", "text"),
            },
            metrics={},
            relations={
                "客户表": RelationTarget(
                    relation=relation,
                    dataset=self.customers,
                    fields={
                        "客户编号": _field("客户编号", "text"),
                        "客户名称": _field("客户名称", "text"),
                    },
                )
            },
            version_no=self.orders.current_version_no,
        )


class Pins:
    """`pin_versions`/`pinned_versions` replaced by a table small enough to read."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def pin_versions(self, db: Any, pins: list[dict[str, Any]]) -> None:
        self.rows.extend(pins)

    async def pinned_versions(self, db: Any, dataset_id: uuid.UUID) -> set[int]:
        return {row["version_no"] for row in self.rows if row["dataset_id"] == dataset_id}

    def by_dataset(self) -> dict[uuid.UUID, int]:
        return {row["dataset_id"]: row["version_no"] for row in self.rows}


@pytest.fixture
def base(monkeypatch: pytest.MonkeyPatch) -> tuple[DatasetService, Base, Pins]:
    state = Base()
    pins = Pins()

    async def dataset(self: Any, dataset_id: Any, ctx: Any, *, write: bool = False) -> Dataset:
        return state.orders

    async def query_context(
        self: Any, dataset: Dataset, *, version_no: Any, ctx: Any
    ) -> QueryContext:
        # What the real one does: the version read is the one asked for, or current.
        state.context.version_no = version_no or state.orders.current_version_no
        return state.context

    async def run_query(db: Any, compiled: Any) -> tuple[list[dict[str, Any]], bool]:
        return ([], False)

    monkeypatch.setattr(DatasetService, "dataset", dataset)
    monkeypatch.setattr(DatasetService, "_query_context", query_context)
    monkeypatch.setattr("app.services.kb_dataset.run_query", run_query)
    monkeypatch.setattr(repo, "pin_versions", pins.pin_versions)
    monkeypatch.setattr(repo, "pinned_versions", pins.pinned_versions)
    return DatasetService(None), state, pins  # type: ignore[arg-type]


async def test_an_answer_pins_the_version_of_every_dataset_it_read(
    base: tuple[DatasetService, Base, Pins],
) -> None:
    """Both the table the question named and the one it was joined to."""
    service, state, pins = base

    await service.query(
        state.orders.id,
        None,
        DatasetQuery(
            dataset="订单",
            fields=[{"field": "订单编号"}, {"field": "客户表.客户名称"}],
            joins=[{"relation": "客户表"}],
        ),
        citation=Citation(tool_call_id=uuid.uuid4(), conversation_id=uuid.uuid4()),
    )

    assert pins.by_dataset() == {state.orders.id: 5, state.customers.id: 2}


async def test_the_pin_names_the_tool_call_that_asked(
    base: tuple[DatasetService, Base, Pins],
) -> None:
    service, state, pins = base
    call, conversation = uuid.uuid4(), uuid.uuid4()

    await service.query(
        state.orders.id,
        None,
        DatasetQuery(dataset="订单", fields=[{"field": "订单编号"}]),
        citation=Citation(tool_call_id=call, conversation_id=conversation),
    )

    [row] = pins.rows
    assert (row["tool_call_id"], row["conversation_id"]) == (call, conversation)


async def test_a_version_already_pinned_is_not_pinned_again(
    base: tuple[DatasetService, Base, Pins],
) -> None:
    """A retried tool call must not add a row: the table would grow with the retries."""
    service, state, pins = base
    request = DatasetQuery(dataset="订单", fields=[{"field": "订单编号"}])
    citation = Citation(tool_call_id=uuid.uuid4())

    await service.query(state.orders.id, None, request, citation=citation)
    await service.query(state.orders.id, None, request, citation=citation)

    assert len(pins.rows) == 1


async def test_a_query_without_a_citation_pins_nothing(
    base: tuple[DatasetService, Base, Pins],
) -> None:
    """A console preview is not an answer, and retention is not asked to keep it."""
    service, state, pins = base

    await service.query(
        state.orders.id, None, DatasetQuery(dataset="订单", fields=[{"field": "订单编号"}])
    )

    assert pins.rows == []


async def test_only_the_datasets_actually_read_are_pinned(
    base: tuple[DatasetService, Base, Pins],
) -> None:
    """A registered relation the statement did not use is not a source."""
    service, state, pins = base

    await service.query(
        state.orders.id,
        None,
        DatasetQuery(dataset="订单", fields=[{"field": "订单编号"}]),
        citation=Citation(tool_call_id=uuid.uuid4()),
    )

    assert pins.by_dataset() == {state.orders.id: 5}


async def test_a_version_read_as_of_an_older_one_is_the_one_pinned(
    base: tuple[DatasetService, Base, Pins],
) -> None:
    """`as_of_version` is the version the answer describes, so it is what survival means."""
    service, state, pins = base

    await service.query(
        state.orders.id,
        None,
        DatasetQuery(dataset="订单", fields=[{"field": "订单编号"}], as_of_version=3),
        citation=Citation(tool_call_id=uuid.uuid4()),
    )

    assert pins.by_dataset() == {state.orders.id: 3}
