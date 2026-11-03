"""The HTTP face of a dataset: thin, and correctly shaped (T29).

Each handler here is two lines - resolve the service, delegate - so what is worth
asserting is not the delegation but the part the OpenAPI schema does not say:

- the two listings report their **own** total rather than the page's length, and
  the ones that wrap their items in an envelope do it here, not in the service;
- the query parameters reach the service under the names it expects, which is the
  one thing a rename on either side silently breaks;
- the upload reads the file and gives it a name when the client did not.

The service is a mock on purpose: this file is about the wire, and the behaviour
behind it is covered against real Postgres in
`tests/integration/test_kb_dataset_service.py`.
"""

import uuid
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile

from app.api.routes.v1 import kb_datasets as routes
from app.core.permissions import AuthContext
from app.main import app
from app.schemas.kb_dataset import (
    DatasetCreate,
    DatasetFieldInput,
    DatasetImportCommit,
    DatasetImportSheet,
    DatasetRowRestore,
    DatasetRowUpdate,
    DatasetUpdate,
)

pytestmark = pytest.mark.anyio

CTX = AuthContext(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="owner")


def _service(**methods: Any) -> Any:
    return MagicMock(**{name: AsyncMock(return_value=value) for name, value in methods.items()})


def _upload(filename: str | None = "订单.xlsx") -> UploadFile:
    return UploadFile(file=BytesIO(b"workbook"), filename=filename)


def _create_body(kb_id: uuid.UUID) -> DatasetCreate:
    return DatasetCreate(
        knowledge_base_id=kb_id,
        name="订单明细",
        fields=[DatasetFieldInput(name="订单编号", data_type="text", is_primary_key=True)],
    )


# -- where the totals are counted -------------------------------------------


async def test_the_dataset_listing_reports_its_own_total() -> None:
    service = _service(list_datasets=[object(), object()])

    answer = await routes.list_datasets(uuid.uuid4(), service, CTX)

    assert answer["total"] == 2


async def test_the_relation_listing_wraps_and_counts_what_it_answered() -> None:
    """The canvas wants edges and a number; the service returns a list."""
    service = _service(relations=[{"name": "客户表"}])

    answer = await routes.list_relations(uuid.uuid4(), service, CTX)

    assert answer == {"items": [{"name": "客户表"}], "total": 1}


async def test_the_candidate_listing_is_shaped_like_the_relation_one() -> None:
    service = _service(relation_candidates=[{"name": "to_客户"}])

    answer = await routes.relation_candidates(uuid.uuid4(), service, CTX)

    assert answer["total"] == 1


async def test_a_created_relation_answers_with_both_halves() -> None:
    """`warnings` is why the route does not use a response model: an inferred
    join that is probably right comes with what the inference was unsure about."""
    service = _service(create_relation=({"id": uuid.uuid4()}, ["字段类型不同"]))

    answer = await routes.create_relation(uuid.uuid4(), MagicMock(), service, CTX)

    assert answer["warnings"] == ["字段类型不同"]


# -- what the section binds to ----------------------------------------------


async def test_a_dataset_base_answers_with_the_fields_the_picker_needs() -> None:
    # `name` is MagicMock's own argument, so the row is a namespace, not a mock.
    base = SimpleNamespace(
        id=uuid.uuid4(),
        name="销售",
        description=None,
        scope="org",
        kb_type="dataset",
        reasoning_provider=None,
        reasoning_model=None,
        reasoning_base_url=None,
        reasoning_secret_id=None,
    )
    service = _service(create_base=base)

    answer = await routes.create_dataset_base(MagicMock(), service, CTX)

    assert answer == {
        "id": base.id,
        "name": "销售",
        "description": None,
        "scope": "org",
        "kb_type": "dataset",
        "reasoning_provider": None,
        "reasoning_model": None,
        "reasoning_base_url": None,
        "has_reasoning_key": False,
    }


async def test_a_dataset_base_names_the_model_that_reads_its_workbooks() -> None:
    """Whose model it is, and whether a key was stored - never the key itself."""
    base = SimpleNamespace(
        id=uuid.uuid4(),
        name="报价",
        description=None,
        scope="org",
        kb_type="dataset",
        reasoning_provider="ollama",
        reasoning_model="qwen3:32b",
        reasoning_base_url="http://10.0.0.9:11434/v1",
        reasoning_secret_id=uuid.uuid4(),
    )
    service = _service(create_base=base)

    answer = await routes.create_dataset_base(MagicMock(), service, CTX)

    assert answer["reasoning_provider"] == "ollama"
    assert answer["reasoning_model"] == "qwen3:32b"
    assert answer["reasoning_base_url"] == "http://10.0.0.9:11434/v1"
    assert answer["has_reasoning_key"] is True
    assert "reasoning_secret_id" not in answer


async def test_the_advice_route_asks_again_only_when_told_to() -> None:
    service = _service(analyze_import={"ran": False, "reason": "no_model"})
    kb_id, import_id = uuid.uuid4(), uuid.uuid4()

    answer = await routes.analyze_import(kb_id, import_id, service, CTX, force=True)

    assert answer == {"ran": False, "reason": "no_model"}
    service.analyze_import.assert_awaited_once_with(kb_id, import_id, CTX, force=True)


async def test_a_dataset_is_created_in_the_base_the_path_names() -> None:
    """The path is the authority: a body that disagrees is corrected, not refused."""
    kb_id = uuid.uuid4()
    service = _service(create="created")

    await routes.create_dataset(kb_id, _create_body(uuid.uuid4()), service, CTX)

    service.create.assert_awaited_once()
    assert service.create.await_args.args[0] == kb_id


# -- the parameters the service reads ---------------------------------------


async def test_a_row_page_passes_its_window_under_the_names_the_service_reads() -> None:
    service = _service(list_rows="page")

    answer = await routes.list_rows(
        uuid.uuid4(), service, CTX, version=3, search="SO", skip=20, limit=100
    )

    assert answer == "page"
    assert service.list_rows.await_args.kwargs == {
        "version_no": 3,
        "search": "SO",
        "skip": 20,
        "limit": 100,
    }


async def test_a_comparison_names_both_versions_and_the_limit() -> None:
    service = _service(compare="diff")

    answer = await routes.compare_versions(uuid.uuid4(), 1, 4, service, CTX, limit=50)

    assert answer == "diff"
    assert service.compare.await_args.kwargs == {"from_version": 1, "to_version": 4, "limit": 50}


async def test_a_hand_edit_carries_its_reason_and_the_version_it_was_made_against() -> None:
    """The reason is required, and the version is how two editors find each other."""
    body = DatasetRowUpdate(values={"金额": 1}, reason="客户更正", expected_version=2)
    service = _service(update_row={"version_no": 3, "record_key": "so-1"})

    answer = await routes.update_row(uuid.uuid4(), "so-1", body, service, CTX)

    assert answer["version_no"] == 3
    assert service.update_row.await_args.kwargs == {
        "record_key": "so-1",
        "values": {"金额": 1},
        "reason": "客户更正",
        "expected_version": 2,
    }


async def test_a_deletion_passes_the_reason_that_travels_in_the_query_string() -> None:
    service = _service(delete_row={"version_no": 2})

    answer = await routes.delete_row(
        uuid.uuid4(), "so-1", service, CTX, reason="重复", expected_version=1
    )

    assert answer == {"version_no": 2}
    assert service.delete_row.await_args.kwargs == {
        "record_key": "so-1",
        "reason": "重复",
        "expected_version": 1,
    }


async def test_a_restore_does_not_ask_for_a_version() -> None:
    """Undoing a deletion is not a competing edit: the row is already settled."""
    service = _service(restore_row={"version_no": 3})

    await routes.restore_row(
        uuid.uuid4(), "so-1", DatasetRowRestore(reason="并非重复"), service, CTX
    )

    assert service.restore_row.await_args.kwargs == {
        "record_key": "so-1",
        "reason": "并非重复",
        "expected_version": None,
    }


async def test_a_rename_goes_through_the_update_route() -> None:
    service = _service(rename="renamed")

    answer = await routes.update_dataset(uuid.uuid4(), DatasetUpdate(name="新名字"), service, CTX)

    assert answer == "renamed"


# -- the import wizard ------------------------------------------------------


async def test_an_upload_reads_the_file_and_answers_with_the_import() -> None:
    record = MagicMock(
        id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        status="preview",
        filename="订单.xlsx",
        file_size=8,
        sheet_count=1,
        plan={"sheets": [{"sheet_name": "订单"}]},
        summary={},
        error=None,
        created_at=None,
        updated_at=None,
        committed_at=None,
    )
    service = _service(preview_import=record)

    answer = await routes.preview_import(uuid.uuid4(), service, CTX, _upload())

    assert answer["sheets"] == [{"sheet_name": "订单"}]
    assert service.preview_import.await_args.kwargs == {
        "filename": "订单.xlsx",
        "content": b"workbook",
    }


async def test_an_upload_without_a_filename_is_given_one() -> None:
    """`multipart/form-data` does not require the field, and a record with no
    filename cannot be shown in the history panel."""
    service = _service(preview_import=MagicMock(plan={}, summary={}))

    await routes.preview_import(uuid.uuid4(), service, CTX, _upload(filename=None))

    assert service.preview_import.await_args.kwargs["filename"] == "workbook.xlsx"


async def test_a_commit_is_accepted_rather_than_waited_for() -> None:
    """202: the worker publishes, and the poller reads `import_status`."""
    commit = DatasetImportCommit(sheets=[DatasetImportSheet(sheet_name="订单")])
    service = _service(queue_import={"import_id": "x", "status": "queued"})

    answer = await routes.commit_import(uuid.uuid4(), uuid.uuid4(), commit, service, CTX)

    assert answer["status"] == "queued"
    assert service.queue_import.await_args.args[3] is commit


async def test_the_history_panel_asks_for_a_page() -> None:
    service = _service(imports={"items": [], "total": 0})

    answer = await routes.list_imports(uuid.uuid4(), service, CTX, skip=40, limit=20)

    assert answer["total"] == 0
    assert service.imports.await_args.kwargs == {"skip": 40, "limit": 20}


# -- where the routes are mounted -------------------------------------------


def test_every_dataset_route_is_reachable_where_the_console_calls_it() -> None:
    """`/kb` is written in the module, so it must not be added again on mount.

    It was, and all 19 routes were published at `/kb/kb/...` while the console
    called `/api/v1/kb/datasets/bases` - every dataset screen answered 404, with
    a 404 that looked like a missing feature rather than a doubled path.

    The mounted paths are what is asserted, not the decorators: the decorator was
    right, and only the mount made it wrong.
    """
    paths = [path for path in app.openapi()["paths"] if "/datasets" in path]

    assert paths, "no dataset route is mounted at all"
    assert [path for path in paths if "/kb/kb/" in path] == []
    # The three shapes the console opens: a base, a dataset in one, and the
    # model's reading of a workbook.
    assert "/api/v1/kb/datasets/bases" in paths
    assert "/api/v1/kb/{kb_id}/datasets" in paths
    assert "/api/v1/kb/{kb_id}/datasets/imports/{import_id}/analyze" in paths
