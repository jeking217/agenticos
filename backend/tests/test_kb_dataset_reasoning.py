"""What the model's advice is allowed to change, and what it is not.

No provider is called here. `advise` is exercised only through the branch that
never reaches a network - no model configured - and the rest is
`digest` / `apply` / the relation filter, which are pure functions over a parsed
workbook. That is deliberate: the interesting part of this module is not "it asks
a model", it is "a wrong answer cannot reach the database".
"""

import asyncio
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from openpyxl import Workbook

from app.services.kb_dataset import excel, reasoning


def proposal_for(header: list[Any], rows: list[list[Any]], title: str = "订单") -> Any:
    book = Workbook()
    sheet = book.active
    sheet.title = title
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    book.save(buffer)
    return excel.scan(buffer.getvalue())[0]


def orders() -> Any:
    """A fresh proposal each time: `apply` corrects the object it is given."""
    return proposal_for(
        ["订单编号", "客户", "金额", "下单日期", "是否加急"],
        [
            ["SO-1", "华东贸易", 1200.5, "2026-01-03", "是"],
            ["SO-2", "北方实业", 300, "2026-01-04", "否"],
            ["SO-3", "南方科技", 4500, "2026-01-05", "否"],
        ],
    )


def lines() -> Any:
    return proposal_for(
        ["行号", "订单编号", "商品", "数量"],
        [["1", "SO-1", "纸箱", 2], ["2", "SO-2", "胶带", 5], ["3", "SO-3", "标签", 9]],
        title="订单明细",
    )


def field(proposal: Any, name: str) -> Any:
    return next(column for column in proposal.fields if column.name == name)


def test_no_provider_means_no_model() -> None:
    kb = SimpleNamespace(reasoning_provider=None, reasoning_model=None)

    assert reasoning.configured(kb) is False


def test_advise_says_why_it_is_empty_rather_than_raising() -> None:
    """An import is never blocked by its advice, so a missing model is a sentence."""

    async def run() -> reasoning.Outcome:
        return await reasoning.advise(
            None, SimpleNamespace(reasoning_provider=None, reasoning_model=None), [orders()]
        )

    outcome = asyncio.run(run())

    assert outcome.ran is False
    assert outcome.reason == "no_model"
    assert outcome.advice is None


def test_digest_describes_columns_without_shipping_the_rows() -> None:
    text = reasoning.digest([orders()])

    assert "订单编号" in text
    assert "parsed_as" in text
    assert "SO-1" in text  # a sample value, not a row
    assert "workbook" in text


def test_advice_retypes_a_column_and_says_so() -> None:
    proposal = orders()
    advice = reasoning.Advice(
        sheets=[
            reasoning.SheetAdvice(
                sheet_name="订单",
                fields=[
                    reasoning.FieldAdvice(name="金额", data_type="integer"),
                    reasoning.FieldAdvice(name="订单编号", data_type="text", is_primary_key=True),
                ],
            )
        ]
    )

    applied = reasoning.apply([proposal], advice)

    assert field(proposal, "金额").data_type == "integer"
    assert field(proposal, "订单编号").is_primary_key is True
    # The key the parser had already guessed needs no change, so it is not
    # reported as one; only the retyped column is.
    assert applied.fields["订单"] == {"金额"}
    # The preview is typed again, so the screen and the model agree.
    assert proposal.preview[0]["金额"] == 1200


def test_advice_about_a_column_that_does_not_exist_is_dropped() -> None:
    proposal = orders()
    advice = reasoning.Advice(
        sheets=[
            reasoning.SheetAdvice(
                sheet_name="订单",
                fields=[reasoning.FieldAdvice(name="不存在的列", data_type="date")],
            )
        ]
    )

    applied = reasoning.apply([proposal], advice)

    assert applied.fields == {}
    assert [column.name for column in proposal.fields] == [
        "订单编号",
        "客户",
        "金额",
        "下单日期",
        "是否加急",
    ]


def test_advice_about_a_sheet_that_does_not_exist_is_dropped() -> None:
    proposal = orders()

    applied = reasoning.apply(
        [proposal], reasoning.Advice(sheets=[reasoning.SheetAdvice(sheet_name="库存")])
    )

    assert applied.fields == {}
    assert applied.sheets == set()


def test_a_type_outside_the_closed_vocabulary_is_refused() -> None:
    """A model that invents `currency` cannot therefore invent a column type."""
    proposal = orders()
    invented = reasoning.Advice.model_construct(
        sheets=[
            reasoning.SheetAdvice.model_construct(
                sheet_name="订单",
                kind=None,
                fields=[
                    reasoning.FieldAdvice.model_construct(
                        name="金额",
                        data_type="currency",
                        is_nullable=None,
                        is_primary_key=False,
                    )
                ],
                notes=[],
            )
        ],
        relations=[],
    )

    reasoning.apply([proposal], invented)

    assert field(proposal, "金额").data_type == "numeric"


def test_only_one_primary_key_survives() -> None:
    proposal = orders()
    advice = reasoning.Advice(
        sheets=[
            reasoning.SheetAdvice(
                sheet_name="订单",
                fields=[
                    reasoning.FieldAdvice(name="订单编号", data_type="text", is_primary_key=True),
                    reasoning.FieldAdvice(name="客户", data_type="text", is_primary_key=True),
                ],
            )
        ]
    )

    reasoning.apply([proposal], advice)

    assert [column.name for column in proposal.fields if column.is_primary_key] == ["订单编号"]


def test_a_sheet_the_model_calls_a_summary_is_marked_as_one() -> None:
    proposal = orders()

    applied = reasoning.apply(
        [proposal],
        reasoning.Advice(sheets=[reasoning.SheetAdvice(sheet_name="订单", kind="summary")]),
    )

    assert proposal.sheet.kind == "summary"
    assert "订单" in applied.sheets
    # A summary has no table to model: its columns are not corrected.
    assert applied.fields == {}


def test_relations_naming_columns_that_exist_survive() -> None:
    advice = reasoning.Advice(
        relations=[
            reasoning.RelationAdvice(
                from_sheet="订单明细",
                from_fields=["订单编号"],
                to_sheet="订单",
                to_fields=["订单编号"],
                cardinality="n:1",
                confidence=0.87,
                reason="同一个订单号",
            )
        ]
    )

    applied = reasoning.apply([orders(), lines()], advice)

    assert applied.relations == [
        {
            "from_sheet": "订单明细",
            "from_fields": ["订单编号"],
            "to_sheet": "订单",
            "to_fields": ["订单编号"],
            "cardinality": "n:1",
            "confidence": 0.87,
            "reason": "同一个订单号",
        }
    ]


def test_relations_naming_a_column_or_sheet_that_does_not_exist_are_dropped() -> None:
    advice = reasoning.Advice(
        relations=[
            reasoning.RelationAdvice(
                from_sheet="订单明细", from_fields=["客户"], to_sheet="订单", to_fields=["客户"]
            ),
            reasoning.RelationAdvice(
                from_sheet="订单明细", from_fields=["订单编号"], to_sheet="库存", to_fields=["单号"]
            ),
            reasoning.RelationAdvice(
                from_sheet="订单", from_fields=["订单编号"], to_sheet="订单", to_fields=["客户"]
            ),
        ]
    )

    applied = reasoning.apply([orders(), lines()], advice)

    assert applied.relations == []


def test_a_padded_sheet_name_still_matches() -> None:
    proposal = orders()
    advice = reasoning.Advice(
        sheets=[
            reasoning.SheetAdvice(
                sheet_name=" 订单 ",
                fields=[reasoning.FieldAdvice(name="金额", data_type="integer")],
            )
        ]
    )

    reasoning.apply([proposal], advice)

    assert field(proposal, "金额").data_type == "integer"


@pytest.mark.parametrize("name", ["订单", "订单明细"])
def test_digest_reads_every_sheet_of_a_workbook(name: str) -> None:
    assert name in reasoning.digest([orders(), lines()])
