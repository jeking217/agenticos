"""Which datasets the promotion sweep should move, as a decision over counts.

The physical half is in `tests/integration/test_kb_dataset_partitions.py`; this is
the rule. It matters separately because it decides *when* a 200k-row `UPDATE`
happens, and getting it wrong either leaves a pool hot or promotes a thousand
datasets that did not need a relation of their own.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.services.kb_dataset import partitions


def test_an_empty_pool_has_no_candidates() -> None:
    assert partitions.candidates({}, pool_rows=0) == []


def test_a_dataset_past_the_threshold_is_a_candidate() -> None:
    small, large = uuid.uuid4(), uuid.uuid4()

    found = partitions.candidates(
        {small: 10, large: partitions.PROMOTION_ROW_THRESHOLD},
        pool_rows=partitions.PROMOTION_ROW_THRESHOLD + 10,
    )

    assert [candidate.dataset_id for candidate in found] == [large]
    assert "rows" in found[0].reason


def test_a_dataset_just_under_the_threshold_is_left_alone() -> None:
    found = partitions.candidates(
        {uuid.uuid4(): partitions.PROMOTION_ROW_THRESHOLD - 1},
        pool_rows=partitions.PROMOTION_ROW_THRESHOLD - 1,
    )

    assert found == []


def test_one_dataset_holding_most_of_a_pool_is_promoted_whatever_its_size() -> None:
    """The plan's K13: past 70% the pool is really that dataset's relation.

    Two hundred thousand rows is under the size threshold, but it is 80% of the
    pool - whose headers, freeze map and vacuum cost the other tenants are carrying
    for it.
    """

    dominant, other = uuid.uuid4(), uuid.uuid4()

    found = partitions.candidates({dominant: 200_000, other: 50_000}, pool_rows=250_000)

    assert [candidate.dataset_id for candidate in found] == [dominant]
    assert "80% of its pool" in found[0].reason


def test_a_pool_with_no_dominant_dataset_and_no_large_one_promotes_nothing() -> None:
    found = partitions.candidates(
        {uuid.uuid4(): 100_000, uuid.uuid4(): 90_000, uuid.uuid4(): 80_000},
        pool_rows=270_000,
    )

    assert found == []


def test_several_large_datasets_are_ordered_largest_first() -> None:
    """One promotion per sweep would take a year otherwise; the order is the point."""

    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    found = partitions.candidates(
        {first: 600_000, second: 900_000, third: 700_000},
        pool_rows=3_000_000,
    )

    assert [candidate.rows for candidate in found] == [900_000, 700_000, 600_000]


def test_a_dataset_that_is_both_large_and_dominant_is_named_once() -> None:
    only = uuid.uuid4()

    found = partitions.candidates({only: 800_000}, pool_rows=800_000)

    assert len(found) == 1
    assert found[0].dataset_id == only


def test_only_a_dataset_with_its_own_partition_can_be_archived() -> None:
    """A pooled dataset's relation holds other tenants, so there is nothing to detach."""

    pooled = SimpleNamespace(storage_mode="pooled", storage_partition_key=uuid.uuid4())

    with pytest.raises(ValueError):
        asyncio.run(partitions.archive_dataset(object(), pooled))
