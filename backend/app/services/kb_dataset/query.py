"""Compiling a described query into one parameterised statement.

The model never writes SQL. It writes a `DatasetQuery` - fields, filters, group
by, windows, joins by *relation name* - and this module turns that into a single
statement. Everything the model cannot name, it cannot reach:

- a field must be in the dataset's model (or a joined dataset's), matched
  against the field list rather than interpolated,
- a join must follow a relation registered on the root dataset, so "join
  anything to anything" is not expressible,
- an aggregate must be one of six, and a window one of six macros,
- every value is bound, never formatted into the statement,
- the statement runs with a timeout and a row cap.

The result names the versions it read (`version_vector`) so an answer can be
pinned to them.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.db.models.kb_dataset import Dataset, DatasetField, DatasetMetric, DatasetRelation
from app.schemas.kb_dataset import DatasetQuery, Filter, FilterGroup, QueryColumn, SelectItem

SCHEMA = "kb_data"
MAX_LIMIT = 1000
STATEMENT_TIMEOUT_MS = 8000
MAX_FILTER_NODES = 50
MAX_PARAMS = 500
# The root is depth 1, one derived table is depth 2, and nothing goes deeper. This
# is not a performance ceiling - a third level would still be one statement - it is
# the point past which nobody reading the compiled SQL can say what it means, and
# past which "a query" stops being auditable at a glance.
MAX_SUBQUERY_DEPTH = 2
# Rows one derived table may hold. It is the same bound as `MAX_LIMIT` on purpose:
# an `IN` list of more rows than a result is allowed to have is a filter nobody can
# eyeball, and the compiler would be building a list it cannot explain.
MAX_SUBQUERY_ROWS = 1000

_SUBQUERY_OPS = frozenset({"in_subquery", "not_in_subquery", "exists", "not_exists"})

# The one relation every compiled statement reads. A module constant rather than
# a literal in three f-strings because the integration test points it at a
# scratch table: comparing the compiler's output against a hand-written statement
# needs rows, and rows in `dataset_rows` need a dataset, which needs a knowledge
# base and an organization - three inserts of scaffolding to test one statement.
ROWS_TABLE = f"{SCHEMA}.dataset_rows"

_AGGREGATES = {
    "sum": "SUM({expr})",
    "avg": "AVG({expr})",
    "min": "MIN({expr})",
    "max": "MAX({expr})",
    "count": "COUNT({expr})",
    "count_distinct": "COUNT(DISTINCT {expr})",
}

_NUMERIC_TYPES = {"integer", "numeric"}


@dataclass
class RelationTarget:
    relation: DatasetRelation
    dataset: Dataset
    fields: dict[str, DatasetField]


@dataclass(frozen=True)
class _SubScope:
    """The one dataset a derived table may read, and nothing else."""

    alias: str
    fields: dict[str, DatasetField]


@dataclass
class QueryContext:
    dataset: Dataset
    fields: dict[str, DatasetField]
    metrics: dict[str, DatasetMetric]
    relations: dict[str, RelationTarget]
    version_no: int
    extra: dict[str, Any] = field(default_factory=dict)


class _Params:
    """Named binds, with a hard ceiling so a pathological filter cannot grow one."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def bind(self, value: Any) -> str:
        if len(self.values) >= MAX_PARAMS:
            raise BadRequestError(message="The query has too many values to bind")
        name = f"p{len(self.values)}"
        self.values[name] = value
        return f":{name}"


def _q(identifier: str) -> str:
    """Quote an identifier we generated. Never called with user text."""
    return '"' + identifier.replace('"', '""') + '"'


def _field_expr(alias: str, field: DatasetField) -> str:
    # The name is a JSON key inside a string literal, so a quote in it is escaped
    # rather than trusted; the schema refuses one, and this covers rows written
    # before it did.
    key = field.name.replace("'", "''")
    raw = f"{alias}.values->>'{key}'"
    if field.data_type == "integer":
        return f"NULLIF({raw}, '')::bigint"
    if field.data_type == "numeric":
        return f"NULLIF({raw}, '')::numeric"
    if field.data_type == "boolean":
        return f"NULLIF({raw}, '')::boolean"
    # `date` and `timestamp` are stored as ISO-8601 text, which orders correctly
    # as text and compares correctly against an ISO bound value.
    return raw


def _coerce_filter_value(value: Any, data_type: str) -> Any:
    if value is None:
        return None
    if data_type == "integer":
        try:
            return int(float(value))
        except (TypeError, ValueError) as exc:
            raise BadRequestError(message=f"{value!r} is not a whole number") from exc
    if data_type == "numeric":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(message=f"{value!r} is not a number") from exc
    if data_type == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "是"}
    return str(value)


@dataclass
class CompiledQuery:
    sql: str
    params: dict[str, Any]
    columns: list[QueryColumn]
    version_vector: dict[str, int]
    warnings: list[str]
    limit: int


class _Compiler:
    def __init__(self, ctx: QueryContext, query: DatasetQuery) -> None:
        self.ctx = ctx
        self.query = query
        self.params = _Params()
        self.warnings: list[str] = []
        self.aliases: dict[str, str] = {"": "r"}
        self.select_list: list[str] = []
        self.output_columns: list[QueryColumn] = []
        self.output_aliases: dict[str, str] = {}
        self.group_exprs: list[str] = []
        self.join_sql: list[str] = []
        self.node_count = 0
        # name -> the derived table's SQL, exposing exactly one column called `v`
        self.subquery_sql: dict[str, str] = {}
        self.window_aliases: set[str] = set()

    # -- field resolution ---------------------------------------------------

    def _alias_for(self, prefix: str) -> str:
        if prefix not in self.aliases:
            raise BadRequestError(message=f"Unknown relation `{prefix}`; add it to `joins` first")
        return self.aliases[prefix]

    def _resolve_field(self, reference: str) -> tuple[str, DatasetField]:
        prefix, _, name = reference.rpartition(".")
        alias = self._alias_for(prefix)
        if not prefix:
            field = self.ctx.fields.get(name)
        else:
            target = self.ctx.relations.get(prefix)
            if target is None:
                raise BadRequestError(message=f"Unknown relation `{prefix}`")
            field = target.fields.get(name)
        if field is None:
            raise BadRequestError(
                message=f"Unknown field `{reference}`; call describe_dataset to see the model"
            )
        return alias, field

    # -- joins --------------------------------------------------------------

    def _compile_joins(self) -> None:
        if not self.query.joins:
            return
        used: set[str] = set()
        for index, join in enumerate(self.query.joins):
            target = self.ctx.relations.get(join.relation)
            if target is None or target.relation.status == "disabled":
                raise BadRequestError(
                    message=f"`{join.relation}` is not a relation of dataset {self.ctx.dataset.name}"
                )
            if join.relation in used:
                raise BadRequestError(message=f"Relation `{join.relation}` is joined twice")
            used.add(join.relation)

            alias = f"j{index}"
            self.aliases[join.relation] = alias
            if join.alias:
                self.aliases[join.alias] = alias

            on_parts: list[str] = []
            for local, foreign in zip(
                target.relation.local_fields, target.relation.foreign_fields, strict=True
            ):
                local_field = self.ctx.fields.get(local)
                foreign_field = target.fields.get(foreign)
                if local_field is None or foreign_field is None:
                    raise BadRequestError(
                        message=f"Relation `{join.relation}` points at a field that no longer exists"
                    )
                left = f"r.values->>'{local_field.name}'"
                right = f"{alias}.values->>'{foreign_field.name}'"
                if (
                    local_field.data_type in _NUMERIC_TYPES
                    and foreign_field.data_type in _NUMERIC_TYPES
                ):
                    on_parts.append(f"NULLIF({left}, '')::numeric = NULLIF({right}, '')::numeric")
                else:
                    on_parts.append(f"{left} = {right}")

            version = self.query.as_of_version
            version_sql = (
                f"{alias}.valid_to IS NULL"
                if version is None
                else f"{alias}.valid_from <= :__asof AND ({alias}.valid_to IS NULL OR {alias}.valid_to > :__asof)"
            )
            join_word = "INNER JOIN" if target.relation.join_type == "inner" else "LEFT JOIN"
            self.join_sql.append(
                f"{join_word} {ROWS_TABLE} AS {alias} ON "
                + " AND ".join(on_parts)
                + f" AND {version_sql}"
                + f" AND {alias}.dataset_id = :__ds{index}"
                + f" AND {alias}.partition_key = :__pk{index}"
                + f" AND {alias}.is_deleted = false"
            )
            self.params.values[f"__ds{index}"] = target.dataset.id
            self.params.values[f"__pk{index}"] = target.dataset.storage_partition_key

    # -- select -------------------------------------------------------------

    def _compile_select(self) -> None:
        items = self.query.fields
        if not items:
            # No projection given: the model is choosing rows, show it a few
            # columns rather than every column of a wide sheet.
            items = [SelectItem(field=name) for name in list(self.ctx.fields)[:8]]
            self.warnings.append("No fields were named; the first eight are returned")

        has_metric = any(item.metric for item in items)
        for item in items:
            if item.metric:
                metric = self.ctx.metrics.get(item.metric)
                if metric is None:
                    raise BadRequestError(
                        message=f"Unknown metric `{item.metric}`; call describe_dataset to see them"
                    )
                alias = item.alias or metric.name
                if metric.agg == "count" and not metric.field_name:
                    expression = "COUNT(*)"
                else:
                    if not metric.field_name:
                        raise BadRequestError(
                            message=f"Metric `{metric.name}` has no field to aggregate"
                        )
                    table_alias, field = self._resolve_field(metric.field_name)
                    expression = _AGGREGATES[metric.agg].format(
                        expr=_field_expr(table_alias, field)
                    )
                self.select_list.append(f"{expression} AS {_q(alias)}")
                self.output_columns.append(QueryColumn(name=alias, data_type="numeric"))
                self.output_aliases[alias] = alias
                continue

            assert item.field is not None
            alias_base = item.alias or item.field.rpartition(".")[2]
            alias = alias_base
            suffix = 2
            while alias in self.output_aliases:
                alias = f"{alias_base}_{suffix}"
                suffix += 1
            prefix, _, _name = item.field.rpartition(".")
            table_alias = self._alias_for(prefix)
            _, field = self._resolve_field(item.field)
            expression = _field_expr(table_alias, field)
            self.select_list.append(f"{expression} AS {_q(alias)}")
            self.output_columns.append(QueryColumn(name=alias, data_type=field.data_type))
            self.output_aliases[alias] = alias
            if has_metric:
                self.group_exprs.append(expression)

    # -- filters ------------------------------------------------------------

    def _compile_subqueries(self) -> None:
        """Build every derived table, once each, before a filter can name one.

        One level only. The depth rule is enforced here rather than in the schema
        because the schema is the wrong place to explain it: what a caller needs to
        read is that a spec carrying its own subqueries is a third level, and the
        compiler is what knows the limit.
        """

        for index, spec in enumerate(self.query.subqueries):
            if spec.name in self.subquery_sql:
                raise BadRequestError(message=f"Subquery name `{spec.name}` is used twice")
            if spec.subqueries:
                raise BadRequestError(
                    message=(
                        f"A derived table is {MAX_SUBQUERY_DEPTH - 1} level deep at most; "
                        f"`{spec.name}` carries its own subqueries"
                    )
                )
            if spec.limit > MAX_SUBQUERY_ROWS:
                raise BadRequestError(
                    message=(
                        f"Subquery `{spec.name}` asks for {spec.limit} rows; a derived table "
                        f"holds at most {MAX_SUBQUERY_ROWS}"
                    )
                )
            target = self.ctx.relations.get(spec.relation)
            if target is None or target.relation.status == "disabled":
                raise BadRequestError(
                    message=(
                        f"`{spec.relation}` is not a relation of dataset {self.ctx.dataset.name}"
                    )
                )
            field = target.fields.get(spec.field)
            if field is None:
                raise BadRequestError(message=f"`{spec.field}` is not a field of `{spec.relation}`")

            alias = f"sq{index}"
            where = [
                f"{alias}.partition_key = :__sqpk{index}",
                f"{alias}.dataset_id = :__sqds{index}",
                f"{alias}.is_deleted = false",
            ]
            if self.query.as_of_version is None:
                where.append(f"{alias}.valid_to IS NULL")
            else:
                where.append(f"{alias}.valid_from <= :__asof")
                where.append(f"({alias}.valid_to IS NULL OR {alias}.valid_to > :__asof)")
            self.params.values[f"__sqpk{index}"] = target.dataset.storage_partition_key
            self.params.values[f"__sqds{index}"] = target.dataset.id
            if spec.filters is not None:
                where.append(
                    self._compile_filter(
                        spec.filters, scope=_SubScope(alias=alias, fields=target.fields)
                    )
                )
            self.subquery_sql[spec.name] = (
                f"SELECT {_field_expr(alias, field)} AS v "
                f"FROM {ROWS_TABLE} AS {alias} "
                f"WHERE " + " AND ".join(where) + f" LIMIT {spec.limit}"
            )

    def _subquery(self, name: str | None) -> str:
        derived = self.subquery_sql.get(name or "")
        if derived is None:
            raise BadRequestError(
                message=f"Unknown subquery `{name}`; declare it in `subqueries` first"
            )
        return derived

    def _uses_a_window(self, node: Filter | FilterGroup | None) -> bool:
        if node is None:
            return False
        if isinstance(node, FilterGroup):
            children = node.and_ or node.or_ or []
            return any(self._uses_a_window(child) for child in children)
        return "." not in node.field and node.field in self.window_aliases

    @staticmethod
    def _rebuild_group(children: list[Any]) -> Any:
        if not children:
            return None
        if len(children) == 1:
            return children[0]
        return FilterGroup.model_validate({"and": children})

    def _split_filters(self, node: Filter | FilterGroup | None) -> tuple[Any, Any]:
        """Split the filter into (rows, result): what a WHERE can answer, and what cannot.

        A leaf that reads a window alias cannot be a predicate on the rows - the
        value does not exist until the window layer has run - so it has to become a
        WHERE on the wrapped result. `and` conjuncts distribute across the two
        layers without changing the answer; a *disjunction* does not, because
        `A OR rank <= 3` is satisfied by a row that fails both halves if each half
        is applied in a different place. That mix is refused rather than answered
        from one side, which is the kind of quietly-wrong the compiler exists to
        prevent.
        """

        if node is None:
            return None, None
        if isinstance(node, FilterGroup):
            children = node.and_ or node.or_ or []
            if node.and_:
                rows: list[Any] = []
                result: list[Any] = []
                for child in children:
                    child_rows, child_result = self._split_filters(child)
                    if child_rows is not None:
                        rows.append(child_rows)
                    if child_result is not None:
                        result.append(child_result)
                return self._rebuild_group(rows), self._rebuild_group(result)
            mixed = [self._uses_a_window(child) for child in children]
            if any(mixed) and not all(mixed):
                raise BadRequestError(
                    message=(
                        "A filter cannot combine window output and source columns with `or`; "
                        "ask for it in one `and` group, or as two queries"
                    )
                )
            return (None, node) if any(mixed) else (node, None)
        return (None, node) if self._uses_a_window(node) else (node, None)

    def _compile_outer_filter(self, node: Filter | FilterGroup) -> str:
        """A predicate on the window layer's output, which only holds window aliases."""

        if isinstance(node, FilterGroup):
            children = node.and_ or node.or_ or []
            joiner = " AND " if node.and_ else " OR "
            return "(" + joiner.join(self._compile_outer_filter(child) for child in children) + ")"

        if node.field not in self.window_aliases:
            raise BadRequestError(
                message=(
                    f"`{node.field}` is not a window alias, so it cannot be filtered once the "
                    "windows have run; filter the source field instead"
                )
            )
        return self._leaf(_q(node.field), "numeric", node)

    def _compile_filter(self, node: Filter | FilterGroup, *, scope: _SubScope | None = None) -> str:
        self.node_count += 1
        if self.node_count > MAX_FILTER_NODES:
            raise BadRequestError(message="The filter has too many conditions")
        if isinstance(node, FilterGroup):
            children = node.and_ or node.or_ or []
            if not children:
                raise BadRequestError(message="An empty filter group is not a filter")
            joiner = " AND " if node.and_ else " OR "
            return (
                "("
                + joiner.join(self._compile_filter(child, scope=scope) for child in children)
                + ")"
            )

        if scope is None:
            alias, field = self._resolve_field(node.field)
        else:
            field = scope.fields.get(node.field)
            if field is None:
                raise BadRequestError(
                    message=f"Unknown field `{node.field}` in subquery `{scope.alias}`"
                )
            alias = scope.alias
        return self._leaf(_field_expr(alias, field), field.data_type, node)

    def _leaf(self, expression: str, data_type: str, node: Filter) -> str:
        op = node.op

        if op in _SUBQUERY_OPS:
            derived = self._subquery(node.subquery)
            if op in {"in_subquery", "not_in_subquery"}:
                keyword = "IN" if op == "in_subquery" else "NOT IN"
                return f"{expression} {keyword} (SELECT sq.v FROM ({derived}) AS sq)"
            exists = "EXISTS" if op == "exists" else "NOT EXISTS"
            # `_field_expr` already casts a numeric field, so only the derived
            # column needs one here.
            comparison = (
                f"NULLIF(sq.v, '')::numeric = {expression}"
                if data_type in _NUMERIC_TYPES
                else f"sq.v = {expression}"
            )
            return f"{exists} (SELECT 1 FROM ({derived}) AS sq WHERE {comparison})"

        if op == "is_null":
            wants_null = node.value is None or node.value is True
            return f"{expression} IS NULL" if wants_null else f"{expression} IS NOT NULL"

        if op in {"in", "not_in"}:
            values = (
                node.values
                if node.values is not None
                else (node.value if isinstance(node.value, list) else [node.value])
            )
            cleaned = [_coerce_filter_value(v, data_type) for v in values if v is not None]
            if not cleaned:
                # An empty `in` is a filter that matches nothing, not a filter
                # that was forgotten; say so in SQL rather than dropping it.
                return "false" if op == "in" else "true"
            placeholders = ", ".join(self.params.bind(v) for v in cleaned)
            keyword = "IN" if op == "in" else "NOT IN"
            return f"{expression} {keyword} ({placeholders})"

        if op == "between":
            values = node.values or []
            if len(values) != 2:
                raise BadRequestError(message="`between` needs exactly two values")
            low, high = (_coerce_filter_value(v, data_type) for v in values)
            return f"{expression} BETWEEN {self.params.bind(low)} AND {self.params.bind(high)}"

        if op == "like":
            if data_type != "text":
                raise BadRequestError(message="`like` only applies to text fields")
            pattern = str(node.value or "")
            if "%" not in pattern and "_" not in pattern:
                pattern = f"%{pattern}%"
            return f"{expression} ILIKE {self.params.bind(pattern)}"

        value = _coerce_filter_value(node.value, data_type)
        if value is None and op in {"eq", "ne"}:
            return f"{expression} IS NULL" if op == "eq" else f"{expression} IS NOT NULL"
        operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        operator = operators.get(op)
        if operator is None:
            raise BadRequestError(message=f"Unsupported operator `{op}`")
        return f"{expression} {operator} {self.params.bind(value)}"

    # -- windows ------------------------------------------------------------

    @staticmethod
    def _direction(window: Any, *, default_descending: bool) -> str:
        if window.order == "descending":
            return "DESC"
        if window.order == "ascending":
            return "ASC"
        return "DESC" if default_descending else "ASC"

    def _window_frame(
        self, window: Any, *, with_order: bool, default_descending: bool = False
    ) -> str:
        parts: list[str] = []
        if window.partition_by:
            exprs = [_q(self._output_of(name)) for name in window.partition_by]
            parts.append("PARTITION BY " + ", ".join(exprs))
        if with_order and window.order_by:
            direction = self._direction(window, default_descending=default_descending)
            parts.append(f"ORDER BY {_q(self._output_of(window.order_by))} {direction}")
        return " ".join(parts)

    def _output_of(self, name: str) -> str:
        if name not in self.output_aliases:
            raise BadRequestError(
                message=f"`{name}` is not a selected column; windows read the result, not the source"
            )
        return name

    def _window_expressions(self) -> tuple[list[str], list[str]]:
        expressions: list[str] = []
        topn_filters: list[str] = []
        for window in self.query.windows:
            value = _q(self._output_of(window.value))
            alias = window.alias
            # A trend is read forwards in time; a ranking is read largest first.
            frame = self._window_frame(
                window,
                with_order=window.macro in {"mom", "yoy", "cum", "rank"},
                default_descending=window.macro == "rank",
            )
            if window.macro in {"mom", "yoy"}:
                previous = f"LAG({value}, {int(window.offset)}) OVER ({frame})"
                expression = f"({value} - {previous}) / NULLIF({previous}, 0)"
            elif window.macro == "share":
                total = f"SUM({value}) OVER ({frame})"
                expression = f"{value} / NULLIF({total}, 0)"
            elif window.macro == "cum":
                expression = (
                    f"SUM({value}) OVER ({frame} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
                )
            elif window.macro == "rank":
                direction = self._direction(window, default_descending=True)
                partition = self._window_frame(window, with_order=False)
                prefix = f"{partition} " if partition else ""
                expression = f"RANK() OVER ({prefix}ORDER BY {value} {direction})"
            else:  # topn
                direction = self._direction(window, default_descending=True)
                partition = self._window_frame(window, with_order=False)
                prefix = f"{partition} " if partition else ""
                expression = f"RANK() OVER ({prefix}ORDER BY {value} {direction})"
                topn_filters.append(f"{_q(alias)} <= {int(window.n)}")
            expressions.append(f"{expression} AS {_q(alias)}")
            self.output_columns.append(QueryColumn(name=alias))
        return expressions, topn_filters

    # -- assembly -----------------------------------------------------------

    def compile(self) -> CompiledQuery:
        if (
            self.query.as_of_version is not None
            and self.query.as_of_version > self.ctx.dataset.current_version_no
        ):
            raise BadRequestError(message="That version has not been published yet")
        if self.query.as_of_version is not None:
            self.params.values["__asof"] = self.query.as_of_version

        self.window_aliases = {window.alias for window in self.query.windows}
        self._compile_joins()
        self._compile_subqueries()
        self._compile_select()

        where = [
            "r.partition_key = :__pk",
            "r.dataset_id = :__ds",
            "r.is_deleted = false",
        ]
        if self.query.as_of_version is None:
            where.append("r.valid_to IS NULL")
        else:
            where.append("r.valid_from <= :__asof")
            where.append("(r.valid_to IS NULL OR r.valid_to > :__asof)")
        self.params.values["__pk"] = self.ctx.dataset.storage_partition_key
        self.params.values["__ds"] = self.ctx.dataset.id

        # A filter that reads a window alias cannot be answered from the rows, so it
        # is held back for the wrapped result. `and` conjuncts split cleanly; a mixed
        # `or` is refused inside `_split_filters` rather than answered from one side.
        row_filters, result_filters = self._split_filters(self.query.filters)
        if row_filters is not None:
            where.append(self._compile_filter(row_filters))

        group_by = ""
        grouping = [name for name in self.query.group_by if name]
        if grouping:
            group_by = " GROUP BY " + ", ".join(_q(self._output_of(name)) for name in grouping)
        elif self.group_exprs:
            group_by = " GROUP BY " + ", ".join(dict.fromkeys(self.group_exprs))

        base = (
            "SELECT "
            + ", ".join(self.select_list)
            + f" FROM {ROWS_TABLE} AS r"
            + (" " + " ".join(self.join_sql) if self.join_sql else "")
            + " WHERE "
            + " AND ".join(where)
            + group_by
        )

        current = base
        if self.query.windows:
            expressions, topn_filters = self._window_expressions()
            current = "SELECT w.*, " + ", ".join(expressions) + f" FROM ({base}) AS w"
            if topn_filters:
                current = "SELECT * FROM (" + current + ") AS t WHERE " + " AND ".join(topn_filters)
            if result_filters is not None:
                current = (
                    "SELECT * FROM ("
                    + current
                    + ") AS x WHERE "
                    + self._compile_outer_filter(result_filters)
                )
        elif result_filters is not None:
            # Unreachable through the schema - only a window alias sends a filter
            # here, and those only exist when `windows` does - but a compiler that
            # silently dropped the predicate would be worse than one that says so.
            raise BadRequestError(
                message="A filter reads a window alias, but the query declares no windows"
            )

        order_sql = ""
        if self.query.order_by:
            parts: list[str] = []
            for order in self.query.order_by:
                if order.field not in self.output_aliases:
                    self.warnings.append(f"Ignored ordering by `{order.field}`: it is not selected")
                    continue
                parts.append(f"{_q(order.field)} {'DESC' if order.descending else 'ASC'}")
            if parts:
                order_sql = " ORDER BY " + ", ".join(parts)

        limit = min(self.query.limit, MAX_LIMIT)
        sql = f"SELECT * FROM ({current}) AS q{order_sql} LIMIT {limit + 1}"

        versions = {self.ctx.dataset.name: self.ctx.version_no}
        for target in self.ctx.relations.values():
            if target.relation.name in self.aliases:
                versions[target.dataset.name] = (
                    self.query.as_of_version
                    if self.query.as_of_version is not None
                    else target.dataset.current_version_no
                )

        return CompiledQuery(
            sql=sql,
            params=self.params.values,
            columns=self.output_columns,
            version_vector=versions,
            warnings=self.warnings,
            limit=limit,
        )


def compile_query(ctx: QueryContext, query: DatasetQuery) -> CompiledQuery:
    return _Compiler(ctx, query).compile()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


async def run_query(db: AsyncSession, compiled: CompiledQuery) -> tuple[list[dict[str, Any]], bool]:
    await db.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
    result = await db.execute(text(compiled.sql), compiled.params)
    rows = [{key: _jsonable(value) for key, value in row.items()} for row in result.mappings()]
    truncated = len(rows) > compiled.limit
    return rows[: compiled.limit], truncated
