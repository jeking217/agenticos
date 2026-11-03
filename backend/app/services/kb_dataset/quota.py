"""The two limits a dataset base is held to, checked where they can still be refused.

Both are quotas in the plain sense - a number this deployment allows - and both
raise `DatasetQuotaError` rather than `BadRequestError`, because the caller's next
move differs: a malformed request is fixed by fixing the body, a full base is not.

The row ceiling is checked at commit rather than at upload. Preview already reads
the workbook, and refusing there would mean an operator who fixed a sheet's header
row had to re-upload it after the count changed; the publish is the moment a
version is created, so it is the moment the ceiling applies to.

Nothing here counts history: a version's rows are bounded, the versions are
bounded by retention, and the two together are what capacity was measured against.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import DatasetQuotaError
from app.db.models.kb_dataset import Dataset


async def refuse_a_full_base(db: AsyncSession, knowledge_base_id: Any) -> None:
    """Stop creating a dataset in a base that already holds the maximum.

    Archived datasets count. They keep their rows and their history until an
    operator removes them, and letting them fall out of the count would make
    "archive and import again" an unlimited path around the ceiling.
    """

    held = await db.scalar(
        select(func.count())
        .select_from(Dataset)
        .where(Dataset.knowledge_base_id == knowledge_base_id)
    )
    limit = settings.KB_DATASET_MAX_PER_BASE
    if int(held or 0) >= limit:
        raise DatasetQuotaError(
            message=(
                f"This base already holds {limit} datasets, the maximum for one base. "
                "Archive one you no longer import into, or raise KB_DATASET_MAX_PER_BASE."
            ),
            details={"limit": limit, "held": int(held or 0)},
        )


def refuse_an_oversized_version(sheet_name: str, rows: int) -> None:
    """Stop a sheet that carries more rows than one version may hold."""

    limit = settings.KB_DATASET_MAX_ROWS
    if rows <= limit:
        return
    raise DatasetQuotaError(
        message=(
            f"Sheet `{sheet_name}` has {rows} rows; one version holds at most {limit}. "
            "Split the sheet, or raise KB_DATASET_MAX_ROWS."
        ),
        details={"sheet_name": sheet_name, "limit": limit, "rows": rows},
    )
