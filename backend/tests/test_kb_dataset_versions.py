"""Version semantics, on a store small enough to inspect by eye.

The rules these tests pin down are the ones that make an old answer still true:

- a change closes an interval and opens another, never rewrites `values`,
- a record missing from a new file keeps its interval rather than vanishing,
- re-uploading identical rows mints no version at all.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.db.models.kb_dataset import Dataset
from app.repositories import kb_dataset as repo
from app.services.kb_dataset.versions import canonical, digest_rows, publish, record_key


class Store:
    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []
        self.versions: list[dict] = []
        self.touched: list[dict] = []

    def live(self) -> dict[str, SimpleNamespace]:
        return {row.record_key: row for row in self.rows if row.valid_to is None}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    state = Store()

    async def current_rows(db, *, partition_key, dataset_id, for_update=False):
        return [row for row in state.rows if row.valid_to is None]

    async def close_rows(db, *, partition_key, row_ids, version_no):
        for row in state.rows:
            if row.id in row_ids and row.valid_to is None:
                row.valid_to = version_no

    async def insert_rows(db, rows):
        state.rows.extend(SimpleNamespace(**row) for row in rows)

    async def add_version(db, **values):
        state.versions.append(values)

    async def count_current(db, *, partition_key, dataset_id):
        return len([row for row in state.rows if row.valid_to is None and not row.is_deleted])

    async def touch_dataset(db, dataset_id, **values):
        state.touched.append(values)

    monkeypatch.setattr(repo, "current_rows", current_rows)
    monkeypatch.setattr(repo, "close_rows", close_rows)
    monkeypatch.setattr(repo, "insert_rows", insert_rows)
    monkeypatch.setattr(repo, "add_version", add_version)
    monkeypatch.setattr(repo, "count_current", count_current)
    monkeypatch.setattr(repo, "touch_dataset", touch_dataset)
    return state


def _dataset() -> Dataset:
    dataset = Dataset()
    dataset.id = uuid.uuid4()
    dataset.name = "订单"
    dataset.storage_partition_key = uuid.uuid4()
    dataset.current_version_no = 0
    dataset.row_count = 0
    return dataset


ROWS_V1 = [
    {"订单编号": "SO-1", "金额": 100},
    {"订单编号": "SO-2", "金额": 200},
]


def test_the_first_import_opens_one_interval_per_row(store: Store) -> None:
    dataset = _dataset()

    result = asyncio.run(
        publish(None, dataset, ROWS_V1, key_fields=["订单编号"], actor_user_id=None)
    )

    assert result.version_no == 1 and result.published
    assert result.rows_added == 2
    assert len(store.rows) == 2
    assert {row.valid_from for row in store.rows} == {1}
    assert dataset.current_version_no == 1


def test_a_changed_row_closes_its_interval_and_opens_a_new_one(store: Store) -> None:
    dataset = _dataset()
    asyncio.run(publish(None, dataset, ROWS_V1, key_fields=["订单编号"]))
    original = store.live()["so-1"]

    result = asyncio.run(
        publish(
            None,
            dataset,
            [{"订单编号": "SO-1", "金额": 150}, {"订单编号": "SO-2", "金额": 200}],
            key_fields=["订单编号"],
        )
    )

    assert result.rows_changed == 1
    assert original.valid_to == 2
    assert original.values["金额"] == 100  # history is not rewritten
    current = store.live()["so-1"]
    assert current.values["金额"] == 150
    assert current.record_version == 2


def test_a_row_missing_from_a_new_file_is_kept(store: Store) -> None:
    dataset = _dataset()
    asyncio.run(publish(None, dataset, ROWS_V1, key_fields=["订单编号"]))

    result = asyncio.run(
        publish(None, dataset, [{"订单编号": "SO-1", "金额": 100}], key_fields=["订单编号"])
    )

    assert result.version_no == 2
    assert result.rows_removed == 0
    assert "so-2" in store.live()
    assert store.live()["so-2"].record_version == 1
    assert any("absent" in warning for warning in result.warnings)


def test_reimporting_the_same_rows_mints_no_version(store: Store) -> None:
    dataset = _dataset()
    asyncio.run(publish(None, dataset, ROWS_V1, key_fields=["订单编号"]))

    result = asyncio.run(publish(None, dataset, ROWS_V1, key_fields=["订单编号"]))

    assert not result.published
    assert result.version_no == 2  # the number it *would* have taken
    assert dataset.current_version_no == 1
    assert len(store.versions) == 1


def test_a_row_with_no_key_is_skipped_and_reported(store: Store) -> None:
    dataset = _dataset()

    result = asyncio.run(
        publish(None, dataset, [{"订单编号": "", "金额": 1}, *ROWS_V1], key_fields=["订单编号"])
    )

    assert result.skipped == 1
    assert len(store.rows) == 2
    assert any("primary key" in warning for warning in result.warnings)


def test_a_repeated_key_keeps_the_last_occurrence(store: Store) -> None:
    dataset = _dataset()

    result = asyncio.run(
        publish(
            None,
            dataset,
            [{"订单编号": "SO-1", "金额": 1}, {"订单编号": "SO-1", "金额": 2}],
            key_fields=["订单编号"],
        )
    )

    assert result.duplicates == 1
    assert store.live()["so-1"].values["金额"] == 2


def test_record_keys_ignore_case_and_padding_but_not_content() -> None:
    assert record_key({"id": " ACME "}, ["id"]) == record_key({"id": "acme"}, ["id"])
    assert record_key({"id": "acme"}, ["id"]) != record_key({"id": "acme2"}, ["id"])
    assert record_key({"a": 1, "b": 2}, ["a", "b"]) != record_key({"a": 1, "b": 3}, ["a", "b"])


def test_a_digest_does_not_depend_on_row_order() -> None:
    assert digest_rows([{"a": 1}, {"b": 2}]) == digest_rows([{"b": 2}, {"a": 1}])


def test_the_canonical_form_sorts_keys_so_comparison_is_stable() -> None:
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})
