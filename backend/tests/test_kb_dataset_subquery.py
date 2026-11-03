"""Subqueries and window-output filters: what they compile to, and what they refuse.

The refusals are the interesting half. A subquery is a second way to name somebody
else's rows, so every guarantee the join path has - registered relation only, one
dataset per derived table, tenant predicates bound - has to hold here too, and the
depth and row ceilings have to be enforced by something that says so rather than by
a schema that silently accepts a shape nothing will compile.
"""

import uuid

import pytest

from app.core.exceptions import BadRequestError
from app.db.models.kb_dataset import Dataset, DatasetField, DatasetMetric, DatasetRelation
from app.schemas.kb_dataset import DatasetQuery
from app.services.kb_dataset.query import (
    MAX_SUBQUERY_ROWS,
    QueryContext,
    RelationTarget,
    compile_query,
)


def _dataset(name: str, version: int = 7) -> Dataset:
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


def _relation(local: list[str], foreign: list[str]) -> DatasetRelation:
    relation = DatasetRelation()
    relation.name = "客户"
    relation.local_fields = local
    relation.foreign_fields = foreign
    relation.join_type = "inner"
    relation.status = "active"
    return relation


def _context() -> QueryContext:
    dataset = _dataset("订单")
    fields = {
        name: _field(name, data_type)
        for name, data_type in [
            ("订单编号", "text"),
            ("地区", "text"),
            ("金额", "numeric"),
            ("客户编号", "text"),
        ]
    }
    metric = DatasetMetric()
    metric.name = "营收"
    metric.agg = "sum"
    metric.field_name = "金额"

    customer = _dataset("客户")
    customer_fields = [_field("客户编号", "text"), _field("等级", "text")]
    target = RelationTarget(
        relation=_relation(["客户编号"], ["客户编号"]),
        dataset=customer,
        fields={field.name: field for field in customer_fields},
    )
    return QueryContext(
        dataset=dataset,
        fields=fields,
        metrics={"营收": metric},
        relations={"客户": target},
        version_no=dataset.current_version_no,
    )


def _query(**values) -> DatasetQuery:
    base = {"dataset": "订单", "fields": [{"field": "订单编号"}]}
    base.update(values)
    return DatasetQuery.model_validate(base)


# --- IN --------------------------------------------------------------------


def test_an_in_subquery_compiles_to_a_derived_table() -> None:
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

    assert "IN (SELECT sq.v FROM (" in compiled.sql
    # The derived table reads the *related* dataset, with its own tenant predicates.
    assert compiled.params["__sqds0"] is not None
    assert compiled.params["__sqpk0"] is not None
    # The spec's own filter is bound, not formatted in.
    assert "A" in compiled.params.values()
    assert "'A'" not in compiled.sql


def test_a_not_in_subquery_is_the_negation() -> None:
    compiled = compile_query(
        _context(),
        _query(
            subqueries=[{"name": "vip", "relation": "客户", "field": "客户编号"}],
            filters={"field": "客户编号", "op": "not_in_subquery", "subquery": "vip"},
        ),
    )

    assert "NOT IN (SELECT sq.v FROM (" in compiled.sql


# --- EXISTS ----------------------------------------------------------------


def test_exists_correlates_on_the_field_and_casts_when_the_field_is_numeric() -> None:
    context = _context()
    context.relations["客户"].fields["余额"] = _field("余额", "numeric")

    compiled = compile_query(
        context,
        _query(
            subqueries=[{"name": "rich", "relation": "客户", "field": "余额"}],
            filters={"field": "金额", "op": "exists", "subquery": "rich"},
        ),
    )

    assert "EXISTS (SELECT 1 FROM (" in compiled.sql
    # Correlated on a cast comparison, because `values->>` hands back text.
    assert "NULLIF(sq.v, '')::numeric = NULLIF(r.values->>'金额', '')::numeric" in compiled.sql


def test_exists_on_a_text_field_compares_as_text() -> None:
    compiled = compile_query(
        _context(),
        _query(
            subqueries=[{"name": "known", "relation": "客户", "field": "客户编号"}],
            filters={"field": "客户编号", "op": "exists", "subquery": "known"},
        ),
    )

    assert "sq.v = r.values->>'客户编号'" in compiled.sql
    assert "::numeric" not in compiled.sql


def test_a_not_exists_is_the_negation() -> None:
    compiled = compile_query(
        _context(),
        _query(
            subqueries=[{"name": "known", "relation": "客户", "field": "客户编号"}],
            filters={"field": "客户编号", "op": "not_exists", "subquery": "known"},
        ),
    )

    assert "NOT EXISTS (SELECT 1 FROM (" in compiled.sql


# --- refusals --------------------------------------------------------------


def test_an_undeclared_subquery_name_is_refused() -> None:
    with pytest.raises(BadRequestError) as refused:
        compile_query(
            _context(),
            _query(filters={"field": "客户编号", "op": "in_subquery", "subquery": "vip"}),
        )

    assert "Unknown subquery" in refused.value.message


def test_a_relation_that_is_not_registered_is_refused() -> None:
    with pytest.raises(BadRequestError) as refused:
        compile_query(
            _context(),
            _query(subqueries=[{"name": "vip", "relation": "供应商", "field": "编号"}]),
        )

    assert "not a relation" in refused.value.message


def test_a_field_the_related_dataset_does_not_model_is_refused() -> None:
    with pytest.raises(BadRequestError) as refused:
        compile_query(
            _context(),
            _query(subqueries=[{"name": "vip", "relation": "客户", "field": "身份证号"}]),
        )

    assert "not a field of" in refused.value.message


def test_a_subquery_may_not_read_a_third_dataset() -> None:
    """The derived table sees one dataset, so `客户.等级`-style references are refused."""

    with pytest.raises(BadRequestError) as refused:
        compile_query(
            _context(),
            _query(
                subqueries=[
                    {
                        "name": "vip",
                        "relation": "客户",
                        "field": "客户编号",
                        "filters": {"field": "订单编号", "op": "eq", "value": "SO-1"},
                    }
                ],
                filters={"field": "客户编号", "op": "in_subquery", "subquery": "vip"},
            ),
        )

    assert "in subquery" in refused.value.message


def test_a_third_level_is_refused_with_the_limit_in_the_message() -> None:
    with pytest.raises(BadRequestError) as refused:
        compile_query(
            _context(),
            _query(
                subqueries=[
                    {
                        "name": "vip",
                        "relation": "客户",
                        "field": "客户编号",
                        "subqueries": [{"name": "deeper", "relation": "客户", "field": "等级"}],
                    }
                ],
                filters={"field": "客户编号", "op": "in_subquery", "subquery": "vip"},
            ),
        )

    assert "1 level deep at most" in refused.value.message


def test_a_derived_table_may_not_exceed_the_row_ceiling() -> None:
    with pytest.raises(BadRequestError) as refused:
        compile_query(
            _context(),
            _query(
                subqueries=[
                    {
                        "name": "vip",
                        "relation": "客户",
                        "field": "客户编号",
                        "limit": MAX_SUBQUERY_ROWS + 1,
                    }
                ],
                filters={"field": "客户编号", "op": "in_subquery", "subquery": "vip"},
            ),
        )

    assert str(MAX_SUBQUERY_ROWS) in refused.value.message


def test_the_ceiling_itself_is_accepted_and_lands_in_the_statement() -> None:
    compiled = compile_query(
        _context(),
        _query(
            subqueries=[
                {"name": "vip", "relation": "客户", "field": "客户编号", "limit": MAX_SUBQUERY_ROWS}
            ],
            filters={"field": "客户编号", "op": "in_subquery", "subquery": "vip"},
        ),
    )

    assert f"LIMIT {MAX_SUBQUERY_ROWS}" in compiled.sql


def test_a_reused_subquery_name_is_refused() -> None:
    with pytest.raises(BadRequestError) as refused:
        compile_query(
            _context(),
            _query(
                subqueries=[
                    {"name": "vip", "relation": "客户", "field": "客户编号"},
                    {"name": "vip", "relation": "客户", "field": "等级"},
                ],
                filters={"field": "客户编号", "op": "in_subquery", "subquery": "vip"},
            ),
        )

    assert "used twice" in refused.value.message


def test_a_subquery_operator_without_a_name_is_refused_by_the_schema() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _query(filters={"field": "客户编号", "op": "in_subquery"})


def test_a_subquery_name_on_a_plain_operator_is_refused_by_the_schema() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _query(filters={"field": "客户编号", "op": "eq", "value": "x", "subquery": "vip"})


# --- window output ---------------------------------------------------------


def _ranked(**values) -> DatasetQuery:
    base = {
        "dataset": "订单",
        "fields": [{"field": "订单编号"}, {"field": "金额"}],
        "group_by": ["订单编号", "金额"],
        "windows": [{"macro": "rank", "value": "金额", "alias": "金额排名"}],
    }
    base.update(values)
    return DatasetQuery.model_validate(base)


def test_filtering_a_window_alias_wraps_the_result() -> None:
    compiled = compile_query(
        _context(),
        _ranked(filters={"field": "金额排名", "op": "lte", "value": 3}),
    )

    # The predicate is applied to the wrapped layer, after the window has run.
    assert ") AS x WHERE " in compiled.sql
    assert '"金额排名" <= ' in compiled.sql
    # And it is never read off the row: the alias exists only in the wrapped layer.
    assert "r.values->>'金额排名'" not in compiled.sql
    assert 3 in compiled.params.values()


def test_a_source_filter_stays_on_the_rows() -> None:
    compiled = compile_query(
        _context(),
        _ranked(filters={"field": "地区", "op": "eq", "value": "华东"}),
    )

    assert ") AS x WHERE " not in compiled.sql
    assert "r.values->>'地区' = " in compiled.sql


def test_an_and_of_a_source_filter_and_a_window_filter_splits_across_the_layers() -> None:
    compiled = compile_query(
        _context(),
        _ranked(
            filters={
                "and": [
                    {"field": "地区", "op": "eq", "value": "华东"},
                    {"field": "金额排名", "op": "lte", "value": 5},
                ]
            }
        ),
    )

    assert "r.values->>'地区' = " in compiled.sql
    assert ") AS x WHERE " in compiled.sql
    assert '"金额排名" <= ' in compiled.sql


def test_an_or_of_a_source_filter_and_a_window_filter_is_refused() -> None:
    """`地区 = 华东 OR 排名 <= 5` cannot be split: applying each half in a different
    layer would answer with rows that satisfy neither."""

    with pytest.raises(BadRequestError) as refused:
        compile_query(
            _context(),
            _ranked(
                filters={
                    "or": [
                        {"field": "地区", "op": "eq", "value": "华东"},
                        {"field": "金额排名", "op": "lte", "value": 5},
                    ]
                }
            ),
        )

    assert "cannot combine window output" in refused.value.message


def test_an_or_wholly_about_window_output_is_wrapped() -> None:
    compiled = compile_query(
        _context(),
        _ranked(
            filters={
                "or": [
                    {"field": "金额排名", "op": "lte", "value": 3},
                    {"field": "金额排名", "op": "gt", "value": 10},
                ]
            }
        ),
    )

    assert ") AS x WHERE " in compiled.sql
    assert '"金额排名" <= ' in compiled.sql
    assert '"金额排名" > ' in compiled.sql


def test_a_filter_that_names_no_window_alias_and_no_field_is_refused() -> None:
    with pytest.raises(BadRequestError) as refused:
        compile_query(_context(), _ranked(filters={"field": "不存在的列", "op": "eq", "value": 1}))

    assert "Unknown field" in refused.value.message
