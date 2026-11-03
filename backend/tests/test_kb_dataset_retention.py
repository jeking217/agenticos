"""Version retention: what a sweep may remove, and what it may never remove.

The rule the whole feature rests on is that an old answer still says what it said.
A sweep that got this wrong would be invisible: the dataset would keep answering
questions from the current version, and the only symptom would be a conversation
six weeks old whose citation now names a version nobody kept.

So these tests pin the three refusals - current, window, pinned - and the boundary
between them: a row closed at the cutoff version is unreachable from every
surviving version, and one closed after it is not.
"""

import asyncio
import uuid

import pytest

from app.db.models.kb_dataset import Dataset
from app.services.kb_dataset import retention


def dataset(version_no: int = 10) -> Dataset:
    return Dataset(
        id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="订单明细",
        primary_key_fields=["订单编号"],
        current_version_no=version_no,
        storage_partition_key=uuid.uuid4(),
        row_count=3,
    )


class Sweep:
    """`apply` with the two statements it issues replaced by recorders."""

    def __init__(self, versions: list[int], pinned: set[int]) -> None:
        self.versions = versions
        self.pinned = pinned
        self.rows_removed: list[int] = []
        self.versions_removed: list[int] = []

    async def version_numbers(self, db, dataset_id):
        return list(self.versions)

    async def remove_rows_through(self, db, dataset, *, through_version):
        self.rows_removed.append(through_version)
        return 7

    async def remove_versions(self, db, dataset_id, versions):
        self.versions_removed = list(versions)
        return len(versions)


@pytest.fixture
def sweep(monkeypatch: pytest.MonkeyPatch) -> Sweep:
    state = Sweep(versions=[1, 2, 3, 4, 5], pinned=set())

    async def version_numbers(db, dataset_id):
        return await state.version_numbers(db, dataset_id)

    async def remove_rows_through(db, dataset, *, through_version):
        return await state.remove_rows_through(db, dataset, through_version=through_version)

    async def remove_versions(db, dataset_id, versions):
        return await state.remove_versions(db, dataset_id, versions)

    monkeypatch.setattr(retention, "version_numbers", version_numbers)
    monkeypatch.setattr(retention, "remove_rows_through", remove_rows_through)
    monkeypatch.setattr(retention, "remove_versions", remove_versions)
    return state


class FakeSession:
    """Only what `apply` asks of a session: the pinned versions, read as rows."""

    def __init__(self, pinned: set[int]) -> None:
        self.pinned = pinned

    async def execute(self, *_args, **_kwargs):
        return [(version,) for version in sorted(self.pinned)]


def test_the_window_keeps_the_versions_a_person_browses():
    decision = retention.plan(
        current_version_no=5, versions=[5, 3, 1, 4, 2], pinned=set(), keep=3
    )

    assert decision.retained == [3, 4, 5]
    assert decision.purge == [1, 2]
    assert decision.row_cutoff == 3
    assert decision.pinned == []


def test_the_current_version_survives_a_window_that_does_not_include_it():
    # A window of two around a dataset whose current version is older than its
    # history - only reachable if a publish ever wrote a lower number, which the
    # service refuses, but the rule must not depend on that refusal.
    decision = retention.plan(
        current_version_no=1, versions=[1, 2, 3, 4], pinned=set(), keep=2
    )

    assert decision.retained == [1, 3, 4]
    assert decision.purge == [2]
    # The row cutoff is the lowest *surviving* version, not the highest removed:
    # version 1 survives, so rows it can still show stay.
    assert decision.row_cutoff == 1


def test_a_pinned_version_survives_the_window_and_moves_the_cutoff():
    decision = retention.plan(
        current_version_no=5, versions=[1, 2, 3, 4, 5], pinned={2}, keep=2
    )

    assert decision.retained == [2, 4, 5]
    assert decision.purge == [1, 3]
    # The pin keeps version 2 readable, so only rows closed at or before it - which
    # version 2 itself never shows - can go. Judging rows by the highest version
    # removed (3) would delete what the pin was created to keep.
    assert decision.row_cutoff == 2


def test_a_pin_outside_the_history_is_reported_but_does_not_invent_a_version():
    decision = retention.plan(
        current_version_no=2, versions=[1, 2], pinned={9}, keep=2
    )

    assert decision.retained == [1, 2]
    assert decision.purge == []
    assert decision.pinned == []


def test_one_is_raised_to_two_because_a_lone_version_has_nothing_to_compare_against():
    decision = retention.plan(
        current_version_no=3, versions=[1, 2, 3], pinned=set(), keep=1
    )

    assert decision.keep == retention.MINIMUM_KEPT
    assert decision.retained == [2, 3]
    assert decision.purge == [1]


def test_a_dataset_inside_its_window_is_left_alone():
    decision = retention.plan(
        current_version_no=3, versions=[1, 2, 3], pinned=set(), keep=20
    )

    assert decision.purge == []
    assert decision.row_cutoff is None


def test_apply_removes_the_versions_and_then_the_rows_they_last_read(sweep: Sweep):
    target = dataset(version_no=5)
    sweep.versions = [1, 2, 3, 4, 5]

    result = asyncio.run(retention.apply(FakeSession(set()), target, keep=3))

    assert result.versions_removed == [1, 2]
    assert result.rows_removed == 7
    # Rows go first: a failure between the two statements leaves versions naming
    # rows that still exist, which reads as an empty version rather than a
    # half-applied sweep.
    assert sweep.rows_removed == [3]
    assert sweep.versions_removed == [1, 2]
    assert result.changed is True


def test_apply_keeps_a_pinned_version_and_removes_nothing_it_needs(sweep: Sweep):
    target = dataset(version_no=5)
    sweep.versions = [1, 2, 3, 4, 5]

    result = asyncio.run(retention.apply(FakeSession({2}), target, keep=2))

    assert 2 not in result.versions_removed
    assert result.versions_removed == [1, 3]
    assert sweep.rows_removed == [2]


def test_apply_on_a_dataset_inside_its_window_touches_nothing(sweep: Sweep):
    target = dataset(version_no=2)
    sweep.versions = [1, 2]

    result = asyncio.run(retention.apply(FakeSession(set()), target, keep=20))

    assert result.changed is False
    assert sweep.rows_removed == []
    assert sweep.versions_removed == []
