"""Publishing a workbook that has rows nobody can identify (plan S5, T08).

The unit tests next door pin the report. This one pins the consequence, on a real
Postgres and a real `.xlsx`:

- a sheet with a repeated key and a row with no key at all is refused, the refusal
  carries Excel row numbers, and **no version is minted** - the point of checking
  before the write is that a refusal leaves nothing behind;
- the same sheet under `on_invalid="skip"` publishes the rows it can identify and
  records how many it withheld, so the count is in the result rather than in
  somebody's memory;
- the rows kept are the ones a publish would have kept: the last of a repeated
  key, not the first.

Everything runs through `imports.commit`, because a report nobody consults is a
report that never fires.
"""

import uuid
from io import BytesIO
from typing import Any

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge_base import KBScope, KnowledgeBase
from app.db.models.organization import Organization
from app.db.models.user import User
from app.repositories import kb_dataset as repo
from app.schemas.kb_dataset import (
    DatasetFieldInput,
    DatasetImportCommit,
    DatasetImportSheet,
)
from app.services.kb_dataset import excel, imports, versions
from app.services.kb_dataset.model import create_dataset

pytestmark = pytest.mark.anyio

SHEET = "订单"


def _workbook() -> bytes:
    """Four rows: `SO-1` twice, one row with no number at all."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(["订单编号", "金额"])
    sheet.append(["SO-1", 100])  # Excel row 2 - the first of a repeated key
    sheet.append(["SO-1", 200])  # Excel row 3 - the one a publish keeps
    sheet.append(["SO-2", 300])  # Excel row 4
    sheet.append([None, 400])  # Excel row 5 - no identity
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def _base(db: AsyncSession) -> tuple[KnowledgeBase, uuid.UUID]:
    """A dataset base in an organization that exists: the FK is real here."""
    owner = User(email=f"{uuid.uuid4()}@example.com", hashed_password="x", is_active=True)
    db.add(owner)
    await db.flush()
    organization = Organization(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}", created_by_user_id=owner.id
    )
    db.add(organization)
    await db.flush()
    base = KnowledgeBase(
        id=uuid.uuid4(),
        name=f"orders-{uuid.uuid4().hex[:8]}",
        scope=KBScope.ORG.value,
        collection_name=f"orders-{uuid.uuid4().hex[:8]}",
        kb_type="dataset",
        embedding_model="text-embedding-3-small",
        embedding_dim=1536,
        embedding_provider="openrouter",
        organization_id=organization.id,
        owner_user_id=owner.id,
        visibility="org",
    )
    db.add(base)
    await db.flush()
    return base, organization.id


async def _dataset(db: AsyncSession, base: KnowledgeBase) -> Any:
    return await create_dataset(
        db,
        knowledge_base_id=base.id,
        organization_id=base.organization_id,
        name="订单明细",
        fields=[
            DatasetFieldInput(
                name="订单编号", data_type="text", is_nullable=False, is_primary_key=True
            ),
            DatasetFieldInput(name="金额", data_type="integer", is_nullable=True),
        ],
    )


async def _staged(db: AsyncSession, base: KnowledgeBase, path: Any) -> Any:
    """An import record whose staged file is the workbook above.

    `imports.preview` is skipped: it writes into the deployment's media directory,
    which this suite does not own. What is under test starts after the upload.
    """
    content = _workbook()
    path.write_bytes(content)
    [proposal] = excel.scan(content)
    record = await repo.create_import(
        db,
        knowledge_base_id=base.id,
        organization_id=base.organization_id,
        status="preview",
        filename="订单.xlsx",
        file_size=len(content),
        file_digest="0" * 64,
        sheet_count=1,
        plan={"sheets": [imports.proposal_payload(proposal)]},
        summary={},
        created_by=None,
    )
    record.staged_path = str(path)
    await db.flush()
    return record


def _key(number: str) -> str:
    """The stored record key of an order number - normalised, so not `SO-1`."""
    return versions.record_key({"订单编号": number}, ["订单编号"])


def _commit(dataset: Any, *, on_invalid: str) -> DatasetImportCommit:
    return DatasetImportCommit(
        sheets=[DatasetImportSheet(sheet_name=SHEET, dataset_id=dataset.id, on_invalid=on_invalid)]
    )


async def test_a_sheet_with_rows_it_cannot_identify_is_refused_before_any_version(
    db: AsyncSession, tmp_path: Any
) -> None:
    base, _ = await _base(db)
    dataset = await _dataset(db, base)
    record = await _staged(db, base, tmp_path / "orders.xlsx")

    result = await imports.commit(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=base.organization_id,
        payload=_commit(dataset, on_invalid="block"),
        actor_user_id=None,
    )

    assert result["status"] == "failed"
    [sheet] = result["sheets"]
    issues = sheet["details"]["issues"]
    # Row 2 repeats a key, row 5 has none: both are Excel's numbers.
    assert [(issue["code"], issue["rows"]) for issue in issues] == [
        ("primary_key_empty", [5]),
        ("primary_key_duplicate", [2]),
    ]

    await db.refresh(dataset)
    assert dataset.current_version_no == 0
    assert await repo.list_versions(db, dataset.id) == ([], 0)


async def test_skipping_publishes_what_can_be_identified_and_says_what_it_withheld(
    db: AsyncSession, tmp_path: Any
) -> None:
    base, _ = await _base(db)
    dataset = await _dataset(db, base)
    record = await _staged(db, base, tmp_path / "orders.xlsx")

    result = await imports.commit(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=base.organization_id,
        payload=_commit(dataset, on_invalid="skip"),
        actor_user_id=None,
    )

    assert result["status"] == "committed"
    [sheet] = result["sheets"]
    assert sheet["rows_withheld"] == 2
    assert sheet["rows_added"] == 2
    assert sheet["quality"]["has_blocking"] is True

    await db.refresh(dataset)
    assert dataset.current_version_no == 1
    assert dataset.row_count == 2
    keys = {
        row.record_key
        for row in await repo.current_rows(
            db, partition_key=dataset.storage_partition_key, dataset_id=dataset.id
        )
    }
    assert keys == {_key("SO-1"), _key("SO-2")}


async def test_the_row_kept_is_the_one_the_publisher_would_have_kept(
    db: AsyncSession, tmp_path: Any
) -> None:
    """`publish` keeps the last of a repeated key, so withholding the first agrees."""
    base, _ = await _base(db)
    dataset = await _dataset(db, base)
    record = await _staged(db, base, tmp_path / "orders.xlsx")

    await imports.commit(
        db,
        record=record,
        knowledge_base_id=base.id,
        organization_id=base.organization_id,
        payload=_commit(dataset, on_invalid="skip"),
        actor_user_id=None,
    )

    [row] = [
        row
        for row in await repo.current_rows(
            db, partition_key=dataset.storage_partition_key, dataset_id=dataset.id
        )
        if row.record_key == _key("SO-1")
    ]
    assert row.values["金额"] == 200
