"""What is wrong with a sheet before it becomes a version (plan S5, T08).

A workbook from finance is not a table. It has a duplicated order number in row
412, a date column where two cells read "待定", and a key column blank where
somebody merged two rows. Publishing it anyway is the expensive option: the
duplicate silently collapses into one record (publish keeps the last of a
repeated key), the two dates are stored as text, and the blank key becomes a
record nobody can point at. All three are invisible afterwards - the dataset
simply answers questions with the lost row missing.

So this module reports before the publish and, on request, withholds the rows
that cannot be trusted:

- `primary_key_duplicate` - two rows the publisher would treat as one record.
- `primary_key_empty` - a row with no identity at all.
- `type_mismatch` - a cell that is not the column's type and would be kept as
  text, which is where "the numbers do not add up" starts.
- `mostly_empty` - a column blank in most rows, usually a misread header.

The first two block: they decide *which record* a row is, so skipping is the
only honest answer, and `block` is the default. The last two warn: they do not
stop a publish, and a report that cried wolf about them would be ignored on the
day it mattered.

Every issue carries Excel row numbers, capped at `MAX_EXAMPLES` for reading. The
rows a `skip` policy withholds are *not* capped - a policy that stopped at the
twentieth row would publish the twenty-first - so the report keeps those
separately.
"""

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.exceptions import ImportValidationError
from app.services.kb_dataset.versions import record_key

BLOCKING = "blocking"
WARNING = "warning"

KEY_DUPLICATE = "primary_key_duplicate"
KEY_EMPTY = "primary_key_empty"
TYPE_MISMATCH = "type_mismatch"
MOSTLY_EMPTY = "mostly_empty"

MAX_EXAMPLES = 20
"""Row numbers kept per issue for reading. The count is the whole truth."""

MOSTLY_EMPTY_RATE = 0.5
"""A column blank in half its rows is more often a misread header than a real gap."""

SKIP_POLICY = "skip"
BLOCK_POLICY = "block"

TYPED = frozenset({"integer", "numeric", "boolean", "date", "timestamp"})

_NUMERIC_OR_BOOL = frozenset({"integer", "numeric", "boolean"})
_TEMPORAL = frozenset({"date", "timestamp"})


def _misfit(value: Any, data_type: str) -> bool:
    """Whether a coerced cell is not the type its column claims.

    Read against the shape `coerce_rows` leaves behind, not against the raw cell:
    a number becomes `int`/`float`, a boolean becomes `bool`, and a date becomes
    an ISO *string* - so a string in a date column is the normal case, and only
    one that will not parse back is the cell somebody typed by hand.
    """
    if data_type in _NUMERIC_OR_BOOL:
        return isinstance(value, str)
    if data_type in _TEMPORAL and isinstance(value, str):
        with contextlib.suppress(ValueError):
            datetime.fromisoformat(value)
            return False
        return True
    return False


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    message: str
    count: int
    rows: tuple[int, ...] = ()
    column: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "column": self.column,
            "rows": list(self.rows),
        }


@dataclass(frozen=True)
class ColumnQuality:
    name: str
    data_type: str
    nulls: int
    null_rate: float
    mismatches: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "nulls": self.nulls,
            "null_rate": self.null_rate,
            "mismatches": self.mismatches,
        }


@dataclass(frozen=True)
class Report:
    sheet_name: str
    rows: int
    """How many rows the sheet holds."""
    scanned: int
    """How many of them were looked at. Fewer than `rows` only in the preview."""
    columns: tuple[ColumnQuality, ...] = ()
    issues: tuple[Issue, ...] = ()
    withheld: tuple[int, ...] = ()
    """Every Excel row number a `skip` policy would withhold - uncapped."""

    @property
    def sampled(self) -> bool:
        return self.scanned != self.rows

    @property
    def blocking(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == BLOCKING)

    @property
    def has_blocking(self) -> bool:
        return any(issue.severity == BLOCKING for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet_name": self.sheet_name,
            "rows": self.rows,
            "scanned": self.scanned,
            "sampled": self.sampled,
            "has_blocking": self.has_blocking,
            "withheld": len(self.withheld),
            "columns": [column.to_dict() for column in self.columns],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class _Seen:
    """The tallies one pass accumulates, before they become immutable issues."""

    nulls: dict[str, int] = field(default_factory=dict)
    mismatches: dict[str, int] = field(default_factory=dict)
    mismatch_rows: dict[str, list[int]] = field(default_factory=dict)
    empty_key_rows: list[int] = field(default_factory=list)
    duplicate_rows: list[int] = field(default_factory=list)


def _keys_of(row: dict[str, Any], key_fields: Sequence[str]) -> str | None:
    """The record identity of a row, or `None` when the row has none.

    The same `record_key` publish uses, so "duplicate" here means exactly the
    rows the publisher would fold into one - not a lookalike of it.
    """
    values = {name: row.get(name) for name in key_fields}
    if any(value is None or value == "" for value in values.values()):
        return None
    key = record_key(values, key_fields)
    return key if key.strip("\x1f") else None


def report(
    sheet_name: str,
    rows: Sequence[dict[str, Any]],
    *,
    types: dict[str, str],
    key_fields: Sequence[str],
    first_row: int = 2,
    total: int | None = None,
    max_examples: int = MAX_EXAMPLES,
) -> Report:
    """One pass over the rows, and the issues worth reading out of it.

    `first_row` is the Excel row number of `rows[0]` - the header row plus one -
    so every number in the result is the one a person sees in Excel. `total` is
    what the sheet holds when `rows` is only a sample of it.

    A duplicate is reported against every occurrence but the last: publish keeps
    the last of a repeated key, so withholding the earlier ones leaves exactly
    the record the publish would have produced.
    """
    seen = _Seen()
    last: dict[str, int] = {}
    repeats: dict[str, list[int]] = {}

    for index, row in enumerate(rows):
        number = first_row + index
        for name, data_type in types.items():
            value = row.get(name)
            if value is None or value == "":
                seen.nulls[name] = seen.nulls.get(name, 0) + 1
            elif _misfit(value, data_type):
                # `coerce_rows` keeps an unreadable cell as text rather than
                # dropping the row; this is where that is counted.
                seen.mismatches[name] = seen.mismatches.get(name, 0) + 1
                examples = seen.mismatch_rows.setdefault(name, [])
                if len(examples) < max_examples:
                    examples.append(number)

        if not key_fields:
            continue
        key = _keys_of(row, key_fields)
        if key is None:
            seen.empty_key_rows.append(number)
            continue
        if key in last:
            repeats.setdefault(key, []).append(last[key])
        last[key] = index

    for earlier in repeats.values():
        seen.duplicate_rows.extend(first_row + index for index in earlier)

    scanned = len(rows)
    columns = tuple(
        ColumnQuality(
            name=name,
            data_type=data_type,
            nulls=seen.nulls.get(name, 0),
            null_rate=round(seen.nulls.get(name, 0) / scanned, 4) if scanned else 0.0,
            mismatches=seen.mismatches.get(name, 0),
        )
        for name, data_type in types.items()
    )
    return Report(
        sheet_name=sheet_name,
        rows=total if total is not None else scanned,
        scanned=scanned,
        columns=columns,
        issues=tuple(_issues(seen, columns, key_fields, max_examples)),
        withheld=tuple(sorted(seen.empty_key_rows + seen.duplicate_rows)),
    )


def _issues(
    seen: _Seen,
    columns: tuple[ColumnQuality, ...],
    key_fields: Sequence[str],
    max_examples: int,
) -> list[Issue]:
    issues: list[Issue] = []
    names = "、".join(key_fields)
    if seen.empty_key_rows:
        issues.append(
            Issue(
                code=KEY_EMPTY,
                severity=BLOCKING,
                message=f"{len(seen.empty_key_rows)} row(s) have no `{names}`: no identity",
                count=len(seen.empty_key_rows),
                rows=tuple(sorted(seen.empty_key_rows)[:max_examples]),
                column=key_fields[0] if key_fields else None,
            )
        )
    if seen.duplicate_rows:
        issues.append(
            Issue(
                code=KEY_DUPLICATE,
                severity=BLOCKING,
                message=(
                    f"{len(seen.duplicate_rows)} row(s) repeat a `{names}` used elsewhere; "
                    "publishing would keep only the last of each"
                ),
                count=len(seen.duplicate_rows),
                rows=tuple(sorted(seen.duplicate_rows)[:max_examples]),
                column=key_fields[0] if key_fields else None,
            )
        )
    for column in columns:
        if column.mismatches:
            issues.append(
                Issue(
                    code=TYPE_MISMATCH,
                    severity=WARNING,
                    message=(
                        f"{column.mismatches} cell(s) in `{column.name}` are not "
                        f"{column.data_type} and will be kept as text"
                    ),
                    count=column.mismatches,
                    rows=tuple(seen.mismatch_rows.get(column.name, [])),
                    column=column.name,
                )
            )
        if column.null_rate >= MOSTLY_EMPTY_RATE and column.data_type != "text":
            issues.append(
                Issue(
                    code=MOSTLY_EMPTY,
                    severity=WARNING,
                    message=(
                        f"`{column.name}` is empty in {column.null_rate:.0%} of the rows; "
                        "check the header row was read correctly"
                    ),
                    count=column.nulls,
                    column=column.name,
                )
            )
    return issues


def refuse(report: Report) -> None:
    """Stop the sheet, naming the rows - the `block` half of the policy."""
    if not report.has_blocking:
        return
    blocking = report.blocking
    raise ImportValidationError(
        message=(
            f"Sheet `{report.sheet_name}` has "
            f"{sum(issue.count for issue in blocking)} row(s) that cannot be published"
        ),
        details={
            "sheet_name": report.sheet_name,
            "policy": BLOCK_POLICY,
            "issues": [issue.to_dict() for issue in blocking],
            "quality": report.to_dict(),
        },
    )


def refuse_all_withheld(report: Report) -> None:
    """A `skip` policy that left nothing: the sheet is not an import, it is a problem."""
    raise ImportValidationError(
        message=f"Every row of sheet `{report.sheet_name}` was withheld",
        details={
            "sheet_name": report.sheet_name,
            "policy": SKIP_POLICY,
            "quality": report.to_dict(),
        },
    )


def without_invalid(
    rows: Sequence[dict[str, Any]], *, first_row: int, report: Report
) -> list[dict[str, Any]]:
    """The rows whose Excel number the report does not withhold."""
    withheld = set(report.withheld)
    if not withheld:
        return list(rows)
    return [row for index, row in enumerate(rows) if first_row + index not in withheld]
