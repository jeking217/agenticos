"""What the workbook reader decides, on workbooks that look like real exports."""

from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.services.kb_dataset import excel


def _saved(book: Workbook) -> bytes:
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _sheet(header: list[object], rows: list[list[object]], title: str = "订单") -> Workbook:
    book = Workbook()
    sheet = book.active
    sheet.title = title
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    return book


def test_header_after_a_banner_and_a_blank_row() -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "订单明细"
    sheet["A1"] = "2026 年订单报表"
    sheet.merge_cells("A1:E1")
    sheet.append([])
    sheet.append(["订单编号", "客户", "金额", "下单日期", "是否加急"])
    for row in [
        ["SO-1", "华东贸易", 1200.5, datetime(2026, 1, 3), "是"],
        ["SO-2", "北方实业", 300, datetime(2026, 1, 4), "否"],
        ["SO-3", "南方科技", 4500, datetime(2026, 1, 5), "否"],
    ]:
        sheet.append(row)

    parsed = excel.scan(_saved(book))[0]

    assert parsed.sheet.header_row == 3
    assert parsed.sheet.headers == ["订单编号", "客户", "金额", "下单日期", "是否加急"]
    assert parsed.sheet.merged_cells == 1


def test_a_table_of_nothing_but_text_keeps_its_first_row_as_the_header() -> None:
    """Every row of a terms table scores as a header; the last one only wins because
    there is no row under it to mark it as a title. A header with nothing beneath it
    is the end of the table, not its start - the all-text 偏远地区 sheet of a real
    quote workbook parsed as a header row and zero data rows that way.
    """
    book = _sheet(
        ["区域编码", "区域名称", "偏远等级", "说明"],
        [
            ["R-PR", "波多黎各", "不派送", "美国本土渠道不承接"],
            ["R-HI", "夏威夷", "超偏远", "仅部分渠道可接"],
            ["R-US", "美国本土一般偏远", "一般", "以官网查询为准"],
        ],
        title="偏远地区",
    )

    parsed = excel.scan(_saved(book))[0]

    assert parsed.sheet.header_row == 1
    assert parsed.sheet.headers == ["区域编码", "区域名称", "偏远等级", "说明"]
    assert len(parsed.sheet.rows) == 3


def test_types_are_inferred_and_a_unique_column_becomes_the_key() -> None:
    book = _sheet(
        ["编号", "地区", "金额", "日期", "加急"],
        [
            ["SO-1", "华东", 10.5, datetime(2026, 1, 1), "是"],
            ["SO-2", "华北", 20, datetime(2026, 1, 2), "否"],
            ["SO-3", "华南", 30, datetime(2026, 1, 3), "否"],
        ],
    )

    proposal = excel.scan(_saved(book))[0]
    types = {field.name: field.data_type for field in proposal.fields}

    assert types == {
        "编号": "text",
        "地区": "text",
        "金额": "numeric",
        "日期": "date",
        "加急": "boolean",
    }
    assert proposal.primary_key == ["编号"]
    assert proposal.preview[0]["金额"] == 10.5


def test_a_summary_sheet_is_not_proposed_for_import() -> None:
    book = Workbook()
    data = book.active
    data.title = "明细"
    data.append(["编号", "金额"])
    for index in range(3):
        data.append([f"K{index}", index + 1])
    summary = book.create_sheet("汇总")
    summary.append(["合计", 6])

    proposals = excel.scan(_saved(book))

    assert [item.sheet.kind for item in proposals] == ["data", "summary"]
    assert proposals[1].fields == []


def test_a_short_sheet_with_a_header_is_a_table_not_a_summary() -> None:
    book = _sheet(["编号", "数量"], [["K1", 1]])

    proposal = excel.scan(_saved(book))[0]

    assert proposal.sheet.kind == "data"
    assert proposal.sheet.warnings


def test_a_header_keeps_its_language_and_loses_only_whitespace() -> None:
    book = _sheet(["客户 名称", "2026 金额"], [["甲", 1], ["乙", 2], ["丙", 3]])

    proposal = excel.scan(_saved(book))[0]

    assert proposal.sheet.headers == ["客户_名称", "2026_金额"]
    assert proposal.sheet.display_names == ["客户 名称", "2026 金额"]


def test_a_file_that_is_not_a_workbook_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="not an Excel workbook"):
        excel.scan(b"id,name\n1,x\n")
