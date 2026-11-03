"""A compiled subquery has to answer what the hand-written statement answers.

The unit tests say what the compiler *emits*; this says the statement it emits is
correct. Two things can only be seen against Postgres: whether the derived table
routes to the right rows, and whether it stays inside its own tenant - a subquery
that forgot one predicate would return a superset that still looks plausible in a
string assertion.

The rows go into a scratch table and `query.ROWS_TABLE` is pointed at it. The real
`dataset_rows` would need a dataset, which needs a knowledge base, which needs an
organization: three inserts of scaffolding to test one statement.
"""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.kb_dataset import Dataset, DatasetField, DatasetRelation
from app.schemas.kb_dataset import DatasetQuery
from app.services.kb_dataset import query as compiler
from app.services.kb_dataset.query import QueryContext, RelationTarget, compile_query, run_query

pytestmark = pytest.mark.anyio

TABLE = "kb_data.subquery_test_rows"

ORDERS, CUSTOMERS, STRANGER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
ORDER_POOL, CUSTOMER_POOL, STRANGER_POOL = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _dataset(name: str, dataset_id: uuid.UUID, pool: uuid.UUID, version: int = 3) -> Dataset:
    dataset = Dataset()
    dataset.id = dataset_id
    dataset.name = name
    dataset.storage_partition_key = pool
    dataset.current_version_no = version
    return dataset


def _field(name: str, data_type: str) -> DatasetField:
    field = DatasetField()
    field.name = name
    field.data_type = data_type
    return field


def _context() -> QueryContext:
    customers = _dataset("客户", CUSTOMERS, CUSTOMER_POOL)
    relation = DatasetRelation()
    relation.name = "客户"
    relation.local_fields = ["客户编号"]
    relation.foreign_fields = ["客户编号"]
    relation.join_type = "inner"
    relation.status = "active"
    return QueryContext(
        dataset=_dataset("订单", ORDERS, ORDER_POOL),
        fields={
            "订单编号": _field("订单编号", "text"),
            "客户编号": _field("客户编号", "text"),
            "金额": _field("金额", "numeric"),
        },
        metrics={},
        relations={
            "客户": RelationTarget(
                relation=relation,
                dataset=customers,
                fields={"客户编号": _field("客户编号", "text"), "等级": _field("等级", "text")},
            )
        },
        version_no=3,
    )


async def _row(
    db: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    pool: uuid.UUID,
    record_key: str,
    values: dict[str, object],
) -> None:
    await db.execute(
        text(
            f"INSERT INTO {TABLE} "
            "(partition_key, id, dataset_id, record_key, record_version, valid_from, "
            " valid_to, is_deleted, values) "
            "VALUES (:pk, gen_random_uuid(), :ds, :key, 3, 3, NULL, false, "
            "CAST(:values AS jsonb))"
        ),
        {"pk": pool, "ds": dataset_id, "key": record_key, "values": json.dumps(values)},
    )


@pytest.fixture
async def scratch_table(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """A table shaped like `dataset_rows`, holding two organisations' worth of rows."""

    await db.execute(
        text(
            f"""
            CREATE TABLE {TABLE} (
                partition_key uuid NOT NULL,
                id uuid NOT NULL,
                dataset_id uuid NOT NULL,
                record_key text NOT NULL,
                record_version integer NOT NULL,
                valid_from integer NOT NULL,
                valid_to integer,
                is_deleted boolean NOT NULL DEFAULT false,
                values jsonb NOT NULL,
                PRIMARY KEY (partition_key, id)
            )
            """
        )
    )
    for key, values in [
        ("SO-1", {"订单编号": "SO-1", "客户编号": "C-1", "金额": 100}),
        ("SO-2", {"订单编号": "SO-2", "客户编号": "C-2", "金额": 200}),
        ("SO-3", {"订单编号": "SO-3", "客户编号": "C-3", "金额": 300}),
    ]:
        await _row(db, dataset_id=ORDERS, pool=ORDER_POOL, record_key=key, values=values)
    for key, values in [
        ("C-1", {"客户编号": "C-1", "等级": "A"}),
        ("C-2", {"客户编号": "C-2", "等级": "B"}),
    ]:
        await _row(db, dataset_id=CUSTOMERS, pool=CUSTOMER_POOL, record_key=key, values=values)
    # Another tenant's customer, in the same table and the same relation shape. It
    # must never be reachable from the order dataset's subquery.
    await _row(
        db,
        dataset_id=STRANGER,
        pool=STRANGER_POOL,
        record_key="C-3",
        values={"客户编号": "C-3", "等级": "A"},
    )
    await db.commit()

    monkeypatch.setattr(compiler, "ROWS_TABLE", TABLE)
    try:
        yield
    finally:
        await db.rollback()
        await db.execute(text(f"DROP TABLE IF EXISTS {TABLE} CASCADE"))
        await db.commit()


def _query(**values) -> DatasetQuery:
    base = {"dataset": "订单", "fields": [{"field": "订单编号"}]}
    base.update(values)
    return DatasetQuery.model_validate(base)


async def _compiled(db: AsyncSession, statement: str, params: dict) -> list[str]:
    result = await db.execute(text(statement), params)
    return sorted(row[0] for row in result.all())


async def test_an_in_subquery_answers_what_the_hand_written_statement_answers(
    db: AsyncSession, scratch_table: None
) -> None:
    compiled = compile_query(
        _context(),
        _query(
            subqueries=[
                {
                    "name": "vip",
                    "relation": "客户",
                    "field": "客户编号",
                    "filters": {"field": "等级", "op": "eq", "value": "A"},
                }
            ],
            filters={"field": "客户编号", "op": "in_subquery", "subquery": "vip"},
        ),
    )
    rows, _ = await run_query(db, compiled)

    hand_written = await _compiled(
        db,
        f"""
        SELECT o.values->>'订单编号'
        FROM {TABLE} o
        WHERE o.partition_key = :pk AND o.dataset_id = :ds
          AND o.is_deleted = false AND o.valid_to IS NULL
          AND o.values->>'客户编号' IN (
              SELECT c.values->>'客户编号'
              FROM {TABLE} c
              WHERE c.partition_key = :cpk AND c.dataset_id = :cds
                AND c.is_deleted = false AND c.valid_to IS NULL
                AND c.values->>'等级' = 'A'
          )
        """,
        {
            "pk": ORDER_POOL,
            "ds": ORDERS,
            "cpk": CUSTOMER_POOL,
            "cds": CUSTOMERS,
        },
    )

    assert sorted(row["订单编号"] for row in rows) == hand_written == ["SO-1"]
    # Another tenant's C-3 is "A" too, and is in the same table: only the tenant
    # predicate keeps it out, so the assertion above is about isolation as well.
    assert "SO-3" not in [row["订单编号"] for row in rows]


async def test_exists_answers_what_the_hand_written_statement_answers(
    db: AsyncSession, scratch_table: None
) -> None:
    compiled = compile_query(
        _context(),
        _query(
            subqueries=[
                {"name": "known", "relation": "客户", "field": "客户编号"},
            ],
            filters={"field": "客户编号", "op": "exists", "subquery": "known"},
        ),
    )
    rows, _ = await run_query(db, compiled)

    hand_written = await _compiled(
        db,
        f"""
        SELECT o.values->>'订单编号'
        FROM {TABLE} o
        WHERE o.partition_key = :pk AND o.dataset_id = :ds
          AND o.is_deleted = false AND o.valid_to IS NULL
          AND EXISTS (
              SELECT 1 FROM {TABLE} c
              WHERE c.partition_key = :cpk AND c.dataset_id = :cds
                AND c.is_deleted = false AND c.valid_to IS NULL
                AND c.values->>'客户编号' = o.values->>'客户编号'
          )
        """,
        {"pk": ORDER_POOL, "ds": ORDERS, "cpk": CUSTOMER_POOL, "cds": CUSTOMERS},
    )

    # SO-3's customer belongs to another tenant, so it is not "known".
    assert sorted(row["订单编号"] for row in rows) == hand_written == ["SO-1", "SO-2"]


async def test_not_exists_is_the_complement(db: AsyncSession, scratch_table: None) -> None:
    compiled = compile_query(
        _context(),
        _query(
            subqueries=[{"name": "known", "relation": "客户", "field": "客户编号"}],
            filters={"field": "客户编号", "op": "not_exists", "subquery": "known"},
        ),
    )
    rows, _ = await run_query(db, compiled)

    assert sorted(row["订单编号"] for row in rows) == ["SO-3"]


async def test_a_window_alias_filter_runs_after_the_window(
    db: AsyncSession, scratch_table: None
) -> None:
    """The wrapped statement has to see the rank, not the row that produced it."""

    compiled = compile_query(
        _context(),
        _query(
            fields=[{"field": "订单编号"}, {"field": "金额"}],
            group_by=["订单编号", "金额"],
            windows=[{"macro": "rank", "value": "金额", "alias": "金额排名"}],
            filters={"field": "金额排名", "op": "lte", "value": 2},
        ),
    )
    rows, _ = await run_query(db, compiled)

    # Largest first, so the two top ranks are SO-3 (300) and SO-2 (200).
    ranked = sorted((row["金额排名"], row["订单编号"]) for row in rows)
    assert ranked == [(1, "SO-3"), (2, "SO-2")]
