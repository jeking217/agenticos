"""Reading a workbook as a set of candidate tables.

An Excel file is not a table: it is a canvas on which somebody drew one, often
with a title row and a merged banner above it, a totals block below, and three
other sheets that look nothing like the first. This module's job is to make a
defensible guess at that drawing - where the header is, which columns are what
type, which sheet is a summary - and to say what it guessed, so the person in
the wizard can overrule it.

Nothing here touches the database or raises a domain error: it returns data and
warnings, and the caller decides what to refuse.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

# How many rows below the header are inspected to infer types. A thousand is
# enough for a stable guess and small enough that a million-row sheet is still
# parsed once rather than twice.
_TYPE_SAMPLE = 1000

# Above this the parse is refused rather than attempted: the request would hold
# the whole sheet in memory and the response would time out anyway.
MAX_ROWS_PER_SHEET = 500_000

_SUMMARY_NAMES = {
    "summary",
    "total",
    "totals",
    "index",
    "contents",
    "readme",
    "说明",
    "汇总",
    "合计",
    "目录",
    "总表",
    "统计",
}

_KEY_HINTS = ("id", "code", "no", "number", "key", "编号", "编码", "代码", "序号", "单号")

_TRUE = {"true", "yes", "y", "是", "对", "1"}
_FALSE = {"false", "no", "n", "否", "错", "0"}

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y%m%d",
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)


@dataclass
class ParsedSheet:
    name: str
    kind: str = "data"  # data | summary | empty
    header_row: int | None = None
    headers: list[str] = field(default_factory=list)
    display_names: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    merged_cells: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class FieldProposal:
    name: str
    display_name: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool
    distinct_ratio: float
    samples: list[str]


@dataclass
class SheetProposal:
    sheet: ParsedSheet
    fields: list[FieldProposal]
    primary_key: list[str]
    preview: list[dict[str, Any]]


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _identifier(raw: str, ordinal: int) -> str:
    """The field name for a header, kept in the language it was written in.

    A Chinese header becomes `金额`, not `col_3`: the name is what the person
    reads in the maintenance table and what the model must use in a query, and a
    positional name would make every question about "amount" a guess. Field names
    are JSON keys and result aliases, never SQL identifiers, so nothing here has
    to be ASCII - see `DatasetFieldInput.name` for the characters refused.
    """
    text = re.sub(r"\s+", "_", raw.strip())
    text = re.sub(r"[\x00-\x1f]", "", text.replace('"', "").replace("'", "")).strip("_")
    return text[:64] or f"col_{ordinal}"


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        count = seen.get(name, 0) + 1
        seen[name] = count
        result.append(name if count == 1 else f"{name}_{count}")
    return result


def _merged_blocks(sheet: Worksheet) -> list[tuple[int, int, int, int, Any]]:
    """Each merged block as `(top, left, bottom, right, value)`, 1-based.

    The sheet is not written to: openpyxl's placeholders are read-only, and a
    cell holding a banner's text is not what the grid wants anyway. The value is
    copied into the grid after it is read, which keeps the workbook untouched and
    the row numbering aligned with Excel's own.
    """
    blocks: list[tuple[int, int, int, int, Any]] = []
    for merged in list(sheet.merged_cells.ranges):
        value = sheet.cell(row=merged.min_row, column=merged.min_col).value
        if _is_blank(value):
            continue
        blocks.append((merged.min_row, merged.min_col, merged.max_row, merged.max_col, value))
    return blocks


def _apply_merged(grid: list[list[Any]], blocks: list[tuple[int, int, int, int, Any]]) -> int:
    for top, left, bottom, right, value in blocks:
        for row in range(top - 1, min(bottom, len(grid))):
            for column in range(left - 1, right):
                if column < len(grid[row]):
                    grid[row][column] = value
    return len(blocks)


def _header_score(values: list[Any]) -> float:
    """How much a row looks like a header rather than a data row."""
    filled = [v for v in values if not _is_blank(v)]
    if len(filled) < 2:
        return 0.0
    text = sum(1 for v in filled if isinstance(v, str) and not _looks_numeric(v))
    unique = len({str(v).strip() for v in filled})
    return (text / len(filled)) * 0.6 + (unique / len(filled)) * 0.3 + min(len(filled) / 6, 1) * 0.1


def _looks_numeric(value: str) -> bool:
    stripped = value.replace(",", "").replace(" ", "").rstrip("%")
    try:
        float(stripped)
    except ValueError:
        return False
    return True


def _find_header(grid: list[list[Any]], limit: int = 10) -> int | None:
    best_row: int | None = None
    best_score = 0.0
    for index in range(min(limit, len(grid))):
        score = _header_score(grid[index])
        # A header is followed by data; a title is followed by another title.
        if index + 1 < len(grid) and _header_score(grid[index + 1]) > 0.85:
            score -= 0.2
        # The last row leaves no data under it, so it cannot be a header however
        # much it reads like one. A table of nothing but text scores every row
        # alike, and without this the last one wins by being penalised for
        # nothing - the header lands on the final line and the sheet parses as
        # zero rows.
        if index == len(grid) - 1:
            score -= 0.3
        if score > best_score:
            best_score, best_row = score, index
    return best_row if best_score >= 0.5 else None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "").replace(" ", "")
        if not stripped:
            return None
        percent = stripped.endswith("%")
        if percent:
            stripped = stripped[:-1]
        try:
            number = float(stripped)
        except ValueError:
            return None
        return number / 100 if percent else number
    return None


def _parse_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        # Excel has no timezone, and the column type is `date`: midnight UTC is the
        # neutral instant that keeps a date a date when it is stored as a timestamp.
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        text = value.strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


def _infer_type(values: list[Any]) -> tuple[str, bool]:
    """The narrowest type that holds every non-empty value, and whether it is nullable."""
    present = [v for v in values if not _is_blank(v)]
    nullable = len(present) < len(values)
    if not present:
        return "text", True

    if all(isinstance(v, bool) for v in present):
        return "boolean", nullable
    as_text = [_text(v).lower() for v in present]
    if all(v in _TRUE or v in _FALSE for v in as_text) and len(set(as_text)) <= 2:
        return "boolean", nullable

    numbers = [_as_number(v) for v in present]
    if all(n is not None for n in numbers):
        integral = all(isinstance(n, float) and n.is_integer() for n in numbers)
        return ("integer" if integral else "numeric"), nullable

    dates = [_parse_date(v) for v in present]
    if all(d is not None for d in dates):
        has_time = any(
            isinstance(v, datetime) and (v.hour or v.minute or v.second) for v in present
        )
        return ("timestamp" if has_time else "date"), nullable

    return "text", nullable


def _coerce(value: Any, data_type: str) -> Any:
    """The stored form of a cell, so comparison between two imports is honest."""
    if _is_blank(value):
        return None
    if data_type == "integer":
        number = _as_number(value)
        return int(number) if number is not None else _text(value)
    if data_type == "numeric":
        number = _as_number(value)
        return round(number, 6) if number is not None else _text(value)
    if data_type == "boolean":
        text = _text(value).lower()
        return text in _TRUE
    if data_type in {"date", "timestamp"}:
        parsed = _parse_date(value)
        return parsed.isoformat() if parsed else _text(value)
    return _text(value)


def _column_values(grid: list[list[Any]], column: int) -> list[Any]:
    return [row[column] if column < len(row) else None for row in grid]


def _suggest_primary_key(
    fields: list[FieldProposal], grid: list[list[Any]], names: list[str]
) -> list[str]:
    unique = [f for f in fields if f.distinct_ratio >= 0.999 and not f.is_nullable]
    if unique:
        hinted = [f.name for f in unique if any(h in f.name.lower() for h in _KEY_HINTS)]
        return (hinted or [unique[0].name])[0:1]

    # No single column is unique: try the pairs, smallest cardinality first, so
    # an orders sheet with repeated customer ids still has a usable key.
    ranked = sorted(fields, key=lambda f: f.distinct_ratio, reverse=True)[:4]
    indexes = {name: position for position, name in enumerate(names)}
    for first in range(len(ranked)):
        for second in range(first + 1, len(ranked)):
            left, right = ranked[first], ranked[second]
            pairs = {
                (_text(row[indexes[left.name]]), _text(row[indexes[right.name]]))
                for row in grid
                if not _is_blank(row[indexes[left.name]])
                and not _is_blank(row[indexes[right.name]])
            }
            if len(pairs) == len(grid) and grid:
                return [left.name, right.name]
    return []


def _is_summary_name(name: str) -> bool:
    normalized = name.strip().lower()
    return any(token in normalized for token in _SUMMARY_NAMES)


def parse_sheet(sheet: Worksheet) -> ParsedSheet:
    blocks = _merged_blocks(sheet)
    if sheet.max_row > MAX_ROWS_PER_SHEET:
        raise ValueError(
            f"Sheet {sheet.title!r} has more than {MAX_ROWS_PER_SHEET:,} rows; "
            "split the workbook before importing it"
        )

    # Every row is kept, blank ones included, so a row number in a warning and a
    # row number in Excel are the same number.
    grid: list[list[Any]] = []
    for row in sheet.iter_rows(values_only=True):
        grid.append(list(row))
    _apply_merged(grid, blocks)
    return _parse_grid(sheet.title, grid, merged_cells=len(blocks))


def _parse_grid(name: str, grid: list[list[Any]], *, merged_cells: int = 0) -> ParsedSheet:
    parsed = ParsedSheet(name=name, merged_cells=merged_cells)
    if not grid:
        parsed.kind = "empty"
        return parsed

    header_index = _find_header(grid)
    if header_index is None:
        parsed.kind = "summary" if _is_summary_name(name) or len(grid) < 3 else "data"
        parsed.warnings.append("No header row was recognised; the first row was used as data")
        parsed.header_row = 1
        parsed.headers = [_identifier(f"col_{i + 1}", i + 1) for i in range(len(grid[0]))]
        parsed.display_names = [_text(v) or f"Column {i + 1}" for i, v in enumerate(grid[0])]
        parsed.rows = grid
        return parsed

    header_cells = grid[header_index]
    parsed.header_row = header_index + 1
    raw_names = [
        _text(v) if not _is_blank(v) else f"Column {i + 1}" for i, v in enumerate(header_cells)
    ]
    parsed.display_names = raw_names
    parsed.headers = _unique_names(
        [_identifier(raw, position + 1) for position, raw in enumerate(raw_names)]
    )
    parsed.rows = [row for row in grid[header_index + 1 :] if any(not _is_blank(v) for v in row)]

    width = len(parsed.headers)
    parsed.rows = [(row + [None] * width)[:width] for row in parsed.rows]
    if _is_summary_name(name):
        parsed.kind = "summary"
    elif len(parsed.rows) < 3:
        # A short sheet with a real header is a short table, not a summary. It is
        # imported, and the warning says why it looks small.
        parsed.warnings.append(
            f"Only {len(parsed.rows)} data row(s); check this is the right sheet"
        )
    if len(parsed.rows) > 200_000:
        parsed.warnings.append(f"{len(parsed.rows):,} rows: the import will run in the background")
    return parsed


def propose_sheet(parsed: ParsedSheet, *, preview_rows: int = 5) -> SheetProposal:
    if parsed.kind != "data" or not parsed.headers:
        return SheetProposal(sheet=parsed, fields=[], primary_key=[], preview=[])

    sample = parsed.rows[:_TYPE_SAMPLE]
    fields: list[FieldProposal] = []
    for position, name in enumerate(parsed.headers):
        values = _column_values(sample, position)
        data_type, nullable = _infer_type(values)
        present = [_text(v) for v in values if not _is_blank(v)]
        distinct = len(set(present))
        fields.append(
            FieldProposal(
                name=name,
                display_name=parsed.display_names[position],
                data_type=data_type,
                is_nullable=nullable,
                is_primary_key=False,
                distinct_ratio=round(distinct / len(values), 4) if values else 0.0,
                samples=present[:3],
            )
        )

    primary_key = _suggest_primary_key(fields, sample, parsed.headers)
    # `column` rather than `field`: `field` is the dataclasses import above, and a
    # loop variable that shadows it is the kind of thing that only breaks later.
    for column in fields:
        column.is_primary_key = column.name in primary_key

    preview = [
        {
            field.name: _coerce(row[position], field.data_type)
            for position, field in enumerate(fields)
        }
        for row in parsed.rows[:preview_rows]
    ]
    if not primary_key:
        parsed.warnings.append(
            "No column uniquely identifies a row; choose a primary key before importing"
        )
    return SheetProposal(sheet=parsed, fields=fields, primary_key=primary_key, preview=preview)


_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _as_bytes(source: bytes | bytearray | BinaryIO | Path | str) -> bytes:
    if isinstance(source, bytes | bytearray):
        return bytes(source)
    if isinstance(source, Path | str):
        return Path(source).read_bytes()
    return source.read()


def _load_xlsx(data: bytes) -> Any:
    return load_workbook(BytesIO(data), read_only=False, data_only=True)


def _xls_grids(data: bytes) -> dict[str, list[list[Any]]]:
    """Read a legacy .xls workbook into plain grids.

    `.xls` predates the format the rest of this module handles; it arrives from
    finance exports and old templates often enough that refusing it would send
    people to a converter for no reason. xlrd gives cells rather than a sheet
    object, so the header logic is shared by rebuilding the same grid shape.
    """
    import xlrd

    book = xlrd.open_workbook(file_contents=data)
    grids: dict[str, list[list[Any]]] = {}
    for sheet in book.sheets():
        rows: list[list[Any]] = []
        for index in range(sheet.nrows):
            values: list[Any] = []
            for cell in sheet.row(index):
                if cell.ctype == xlrd.XL_CELL_DATE:
                    values.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    values.append(bool(cell.value))
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    values.append(None)
                else:
                    values.append(cell.value)
            if all(_is_blank(value) for value in values):
                continue
            rows.append(values)
        grids[sheet.name] = rows
    return grids


def _sheets(source: bytes | bytearray | BinaryIO | Path | str) -> list[ParsedSheet]:
    data = _as_bytes(source)
    if data.startswith(_OLE_MAGIC):
        return [_parse_grid(name, grid) for name, grid in _xls_grids(data).items()]
    if not data.startswith(_ZIP_MAGIC):
        raise ValueError("This file is not an Excel workbook (.xlsx, .xlsm or .xls)")
    workbook = _load_xlsx(data)
    try:
        return [parse_sheet(sheet) for sheet in workbook.worksheets]
    finally:
        workbook.close()


def scan(source: bytes | bytearray | BinaryIO | Path | str) -> list[SheetProposal]:
    """Every sheet of a workbook as a proposal, in workbook order."""
    return [propose_sheet(parsed) for parsed in _sheets(source)]


def sheets_of(source: bytes | bytearray | BinaryIO | Path | str) -> dict[str, ParsedSheet]:
    """Every sheet of a workbook, parsed once and keyed by name.

    The commit path publishes several sheets of one file, and it used to call
    `rows_of` for each of them - which read and parsed the *whole workbook* once
    per sheet, so a six-sheet import parsed the same bytes six times and the cost
    of an import grew with the number of sheets. Measured at a million rows, that
    was most of a re-import's wall time
    (`tests/integration/test_kb_dataset_capacity.py` is the run that found it).

    The caller drops each entry as it publishes it, so a large workbook is not
    held in parsed form and in publishable form at the same time.
    """
    return {parsed.name: parsed for parsed in _sheets(source)}


def coerce_rows(
    parsed: ParsedSheet, data_types: dict[str, str], *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Typed values keyed by column, ready to store.

    A cell that cannot be read as its column's type is kept as text rather than
    dropped: losing a row because one cell has a stray character would make the
    version count wrong, and the quality pass is where that is surfaced.

    `limit` exists for that quality pass: the preview reports on a sample of a
    large sheet, and paying for a full coercion there would double the memory a
    workbook already holds.
    """
    rows: list[dict[str, Any]] = []
    for raw in parsed.rows[:limit] if limit is not None else parsed.rows:
        rows.append(
            {
                name: _coerce(raw[position], data_types.get(name, "text"))
                for position, name in enumerate(parsed.headers)
            }
        )
    return rows
