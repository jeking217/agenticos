"""Reading the sheet before publishing it (plan S5, T08).

The report exists to make three losses visible that a publish hides:

- a repeated key, which `publish` folds into one record by keeping the last of
  them, so the earlier row disappears without an error;
- a row with no key at all, which becomes a record nobody can point at;
- a cell that is not its column's type, which is stored as text and shows up
  later as a number that does not add up.

The assertions are about *which rows get named* and *which policy applies*, not
about message wording: the row numbers are the product here, because a person
opens Excel and goes to the row.
"""

import pytest

from app.core.exceptions import ImportValidationError
from app.services.kb_dataset import quality

TYPES = {"订单编号": "text", "金额": "numeric", "日期": "date"}
KEYS = ["订单编号"]


def _report(rows: list[dict], **overrides: object) -> quality.Report:
    values: dict = {"types": TYPES, "key_fields": KEYS, "first_row": 2}
    values.update(overrides)
    return quality.report("订单明细", rows, **values)


def _codes(report: quality.Report) -> set[str]:
    return {issue.code for issue in report.issues}


def _issue(report: quality.Report, code: str) -> quality.Issue:
    [found] = [issue for issue in report.issues if issue.code == code]
    return found


def test_a_clean_sheet_reports_nothing_but_its_columns() -> None:
    report = _report(
        [
            {"订单编号": "SO-1", "金额": 100.0, "日期": "2026-01-01"},
            {"订单编号": "SO-2", "金额": 200.0, "日期": "2026-01-02"},
        ]
    )

    assert report.issues == ()
    assert not report.has_blocking
    assert report.rows == report.scanned == 2
    assert [(column.name, column.nulls, column.null_rate) for column in report.columns] == [
        ("订单编号", 0, 0.0),
        ("金额", 0, 0.0),
        ("日期", 0, 0.0),
    ]


def test_a_repeated_key_names_every_row_but_the_last() -> None:
    """`publish` keeps the last of a repeated key, so those are the rows it drops."""
    report = _report(
        [
            {"订单编号": "SO-1", "金额": 100.0, "日期": None},  # row 2
            {"订单编号": "SO-2", "金额": 200.0, "日期": None},  # row 3
            {"订单编号": "SO-1", "金额": 150.0, "日期": None},  # row 4
        ]
    )

    duplicate = _issue(report, quality.KEY_DUPLICATE)
    assert duplicate.severity == quality.BLOCKING
    assert (duplicate.count, duplicate.rows) == (1, (2,))
    assert report.withheld == (2,)


def test_a_row_with_no_key_is_named_as_having_none() -> None:
    report = _report(
        [
            {"订单编号": "SO-1", "金额": 100.0, "日期": None},
            {"订单编号": None, "金额": 200.0, "日期": None},  # row 3
            {"订单编号": "  ", "金额": 300.0, "日期": None},  # row 4
        ]
    )

    empty = _issue(report, quality.KEY_EMPTY)
    assert empty.severity == quality.BLOCKING
    assert (empty.count, empty.rows) == (2, (3, 4))
    assert report.withheld == (3, 4)


def test_two_columns_of_a_composite_key_are_one_identity() -> None:
    """`SO-1/甲` and `SO-1/乙` are different records; `SO-1/甲` twice is not."""
    report = quality.report(
        "订单明细",
        [
            {"订单编号": "SO-1", "地区": "甲"},
            {"订单编号": "SO-1", "地区": "乙"},
            {"订单编号": "SO-1", "地区": "甲"},
        ],
        types={"订单编号": "text", "地区": "text"},
        key_fields=["订单编号", "地区"],
        first_row=2,
    )

    assert _issue(report, quality.KEY_DUPLICATE).rows == (2,)
    assert report.withheld == (2,)


def test_a_cell_of_the_wrong_type_is_a_warning_with_the_rows_it_is_in() -> None:
    report = _report(
        [
            {"订单编号": "SO-1", "金额": 100.0, "日期": "2026-01-01"},
            {"订单编号": "SO-2", "金额": "待定", "日期": "2026-01-02"},  # row 3
            {"订单编号": "SO-3", "金额": "N/A", "日期": "2026-01-03"},  # row 4
        ]
    )

    mismatch = _issue(report, quality.TYPE_MISMATCH)
    assert mismatch.severity == quality.WARNING
    assert (mismatch.column, mismatch.count, mismatch.rows) == ("金额", 2, (3, 4))
    # A wrong type does not stop a publish: the value is kept, as text.
    assert not report.has_blocking


def test_a_column_empty_in_most_rows_is_flagged_as_a_likely_misread_header() -> None:
    report = _report(
        [
            {"订单编号": "SO-1", "金额": None, "日期": "2026-01-01"},
            {"订单编号": "SO-2", "金额": None, "日期": "2026-01-02"},
            {"订单编号": "SO-3", "金额": 10.0, "日期": "2026-01-03"},
        ]
    )

    mostly = _issue(report, quality.MOSTLY_EMPTY)
    assert (mostly.column, mostly.count) == ("金额", 2)
    assert report.columns[1].null_rate == pytest.approx(0.6667)


def test_a_text_column_may_be_empty_without_being_called_a_misread_header() -> None:
    """Free text is allowed to be blank; calling that a quality problem is noise."""
    report = _report(
        [
            {"订单编号": "SO-1", "备注": None},
            {"订单编号": "SO-2", "备注": None},
        ],
        types={"订单编号": "text", "备注": "text"},
    )

    assert report.issues == ()


def test_the_row_numbers_are_excel_s_and_not_an_index() -> None:
    """A header on row 3 puts the first data row on 4, and that is what is named."""
    report = _report(
        [
            {"订单编号": "SO-1", "金额": 1.0, "日期": None},
            {"订单编号": "SO-1", "金额": 2.0, "日期": None},
        ],
        first_row=4,
    )

    assert _issue(report, quality.KEY_DUPLICATE).rows == (4,)
    assert report.withheld == (4,)


def test_the_report_does_not_print_every_row_of_a_broken_sheet() -> None:
    """The count is the truth; the list is twenty rows somebody can actually read."""
    rows = [{"订单编号": "", "金额": 1.0, "日期": None} for _ in range(200)]

    report = _report(rows, max_examples=5)

    assert _issue(report, quality.KEY_EMPTY).count == 200
    assert len(_issue(report, quality.KEY_EMPTY).rows) == 5
    # What is withheld is not capped: a policy that stopped at five would publish 195.
    assert len(report.withheld) == 200


def test_a_sample_says_it_is_one_and_reports_the_sheet_s_real_size() -> None:
    report = _report([{"订单编号": "SO-1", "金额": 1.0, "日期": None}], total=500_000)

    assert (report.scanned, report.rows, report.sampled) == (1, 500_000, True)


def test_blocking_refuses_the_sheet_and_carries_the_rows() -> None:
    report = _report(
        [
            {"订单编号": "SO-1", "金额": 1.0, "日期": None},
            {"订单编号": "SO-1", "金额": 2.0, "日期": None},
        ]
    )

    with pytest.raises(ImportValidationError) as raised:
        quality.refuse(report)

    assert raised.value.code == "DATASET_IMPORT_VALIDATION_FAILED"
    assert raised.value.status_code == 422
    details = raised.value.details or {}
    assert details["policy"] == "block"
    assert details["issues"][0]["rows"] == [2]


def test_a_warning_does_not_refuse_the_sheet() -> None:
    report = _report([{"订单编号": "SO-1", "金额": "待定", "日期": None}])

    quality.refuse(report)


def test_skipping_withholds_the_rows_and_keeps_the_rest() -> None:
    rows = [
        {"订单编号": "SO-1", "金额": 1.0, "日期": None},
        {"订单编号": "SO-1", "金额": 2.0, "日期": None},
        {"订单编号": "SO-2", "金额": 3.0, "日期": None},
    ]
    report = _report(rows)

    kept = quality.without_invalid(rows, first_row=2, report=report)

    assert [row["金额"] for row in kept] == [2.0, 3.0]


def test_skipping_every_row_is_refused_rather_than_published_as_nothing() -> None:
    report = _report([{"订单编号": None, "金额": 1.0, "日期": None}])

    with pytest.raises(ImportValidationError, match="Every row"):
        quality.refuse_all_withheld(report)


def test_the_policy_compares_the_same_identity_the_publisher_does() -> None:
    """Whitespace and full-width characters are normalised by `record_key`, so
    `SO-1` and ` SO-1 ` are one record here as they are at publish time."""
    report = _report(
        [
            {"订单编号": "SO-1", "金额": 1.0, "日期": None},
            {"订单编号": "  SO-1  ", "金额": 2.0, "日期": None},
        ]
    )

    assert _issue(report, quality.KEY_DUPLICATE).count == 1


def test_a_sheet_with_no_key_has_nothing_to_be_duplicate_about() -> None:
    """The model refuses a keyless dataset earlier; the report must not pretend to
    have found duplicates in one."""
    report = _report(
        [
            {"订单编号": "SO-1", "金额": 1.0},
            {"订单编号": "SO-1", "金额": 2.0},
        ],
        types={"订单编号": "text", "金额": "numeric"},
        key_fields=[],
    )

    assert report.issues == ()
    assert report.withheld == ()
