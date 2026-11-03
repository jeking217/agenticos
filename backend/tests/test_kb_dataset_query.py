"""The compiler's refusals matter more than its output: they are the boundary.

A test asserting the exact SQL string would break on every formatting change, so
these assert what must be true of any statement it produces - the partition key
is in the predicate, values are bound, only registered relations are joined - and
that everything else is refused with a message the model can act on.
"""

import uuid

import pytest

from app.core.exceptions import BadRequestError
from app.db.models.kb_dataset import Dataset, DatasetField, DatasetMetric, DatasetRelation
from app.schemas.kb_dataset import DatasetQuery
from app.services.kb_dataset.query import QueryContext, RelationTarget, compile_query


def _dataset(name: str = "订单", version: int = 7) -> Dataset:
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


def _context(**relations: tuple[DatasetRelation, Dataset, list[DatasetField]]) -> QueryContext:
    dataset = _dataset()
    fields = {
        name: _field(name, data_type)
        for name, data_type in [
            ("订单编号", "text"),
            ("地区", "text"),
            ("金额", "numeric"),
            ("月份", "text"),
            ("客户编号", "text"),
        ]
    }
    metric = DatasetMetric()
    metric.name = "营收"
    metric.agg = "sum"
    metric.field_name = "金额"
    targets = {
        name: RelationTarget(relation=relation, dataset=target, fields={f.name: f for f in target_fields})
        for name, (relation, target, target_fields) in relations.items()
    }
    return QueryContext(
        dataset=dataset,
        fields=fields,
        metrics={"营收": metric},
        relations=targets,
        version_no=dataset.current_version_no,
    )


def test_every_statement_names_the_partition_and_binds_its_values() -> None:
    ctx = _context()
    compiled = compile_query(
        ctx,
        DatasetQuery(
            dataset="订单",
            fields=[{"field": "地区"}, {"metric": "营收"}],
            filters={"field": "金额", "op": "gte", "value": 100},
            group_by=["地区"],
            limit=10,
        ),
    )

    assert "r.partition_key = :__pk" in compiled.sql
    assert "r.dataset_id = :__ds" in compiled.sql
    assert "r.valid_to IS NULL" in compiled.sql
    assert "100" not in compiled.sql  # the value is a bind, not text
    assert 100.0 in compiled.params.values()
    assert compiled.limit == 10


def test_a_field_outside_the_model_is_refused_rather_than_interpolated() -> None:
    with pytest.raises(BadRequestError, match="Unknown field"):
        compile_query(
            _context(),
            DatasetQuery(dataset="订单", fields=[{"field": "金额; drop table dataset_rows"}]),
        )


def test_like_on_a_numeric_column_is_refused() -> None:
    with pytest.raises(BadRequestError, match="only applies to text"):
        compile_query(
            _context(),
            DatasetQuery(
                dataset="订单",
                fields=[{"field": "金额"}],
                filters={"field": "金额", "op": "like", "value": "1"},
            ),
        )


def test_metrics_group_by_every_field_that_is_not_aggregated() -> None:
    compiled = compile_query(
        _context(),
        DatasetQuery(
            dataset="订单",
            fields=[{"field": "地区"}, {"field": "月份"}, {"metric": "营收"}],
        ),
    )

    assert "GROUP BY" in compiled.sql
    assert 'r.values->>\'地区\'' in compiled.sql


def test_a_join_is_only_allowed_along_a_registered_relation() -> None:
    with pytest.raises(BadRequestError, match="not a relation"):
        compile_query(
            _context(),
            DatasetQuery(dataset="订单", fields=[{"field": "客户.等级"}], joins=[{"relation": "客户"}]),
        )


def test_a_registered_relation_joins_and_reports_both_versions() -> None:
    relation = DatasetRelation()
    relation.name = "to_客户"
    relation.local_fields = ["客户编号"]
    relation.foreign_fields = ["客户编号"]
    relation.join_type = "left"
    relation.status = "confirmed"
    target = _dataset("客户", version=3)
    ctx = _context(to_客户=(relation, target, [_field("客户编号", "text"), _field("等级", "text")]))

    compiled = compile_query(
        ctx,
        DatasetQuery(
            dataset="订单",
            fields=[{"field": "地区"}, {"field": "to_客户.等级"}],
            joins=[{"relation": "to_客户"}],
            filters={"field": "to_客户.等级", "op": "eq", "value": "VIP"},
        ),
    )

    assert "LEFT JOIN kb_data.dataset_rows AS j0" in compiled.sql
    assert "j0.partition_key = :__pk0" in compiled.sql
    assert compiled.version_vector == {"订单": 7, "客户": 3}


def test_a_window_reads_forwards_in_time_and_a_rank_reads_largest_first() -> None:
    trend = compile_query(
        _context(),
        DatasetQuery(
            dataset="订单",
            fields=[{"field": "月份"}, {"metric": "营收"}],
            group_by=["月份"],
            windows=[{"macro": "mom", "value": "营收", "alias": "涨幅", "order_by": "月份"}],
        ),
    )
    rank = compile_query(
        _context(),
        DatasetQuery(
            dataset="订单",
            fields=[{"field": "地区"}, {"metric": "营收"}],
            group_by=["地区"],
            windows=[{"macro": "topn", "value": "营收", "alias": "排名", "n": 3}],
        ),
    )

    assert "LAG(" in trend.sql and 'ORDER BY "月份" ASC' in trend.sql
    assert "RANK()" in rank.sql and 'ORDER BY "营收" DESC' in rank.sql
    assert '"排名" <= 3' in rank.sql


def test_as_of_reads_the_interval_that_covered_that_version() -> None:
    compiled = compile_query(
        _context(), DatasetQuery(dataset="订单", fields=[{"field": "金额"}], as_of_version=3)
    )

    assert "r.valid_from <= :__asof" in compiled.sql
    assert "(r.valid_to IS NULL OR r.valid_to > :__asof)" in compiled.sql


def test_a_version_that_does_not_exist_yet_is_refused() -> None:
    with pytest.raises(BadRequestError, match="not been published"):
        compile_query(
            _context(), DatasetQuery(dataset="订单", fields=[{"field": "金额"}], as_of_version=99)
        )


def test_the_limit_is_capped() -> None:
    compiled = compile_query(
        _context(), DatasetQuery(dataset="订单", fields=[{"field": "金额"}], limit=1000)
    )

    assert compiled.limit == 1000
    assert "LIMIT 1001" in compiled.sql
