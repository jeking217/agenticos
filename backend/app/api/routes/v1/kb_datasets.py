"""Dataset knowledge bases - the HTTP face of `DatasetService`.

A dataset is a knowledge base of a second kind: rows rather than documents,
queried by field rather than searched by meaning. These routes cover the whole
life of one: agree a model, import sheets into versions, read any version, keep
or remove a row, compare two versions, and ask a question the compiler turns
into one statement.

**What a refusal looks like.** `{"error": {"code", "message", "details"}}`, with
these codes stable:

- `NOT_FOUND` (404): no such knowledge base, dataset or row - also what another
  tenant's looks like.
- `AUTHORIZATION_ERROR` (403): the caller can read the base but not change it.
- `BAD_REQUEST` (400): the request does not fit the data model - an unknown
  field, a join that is not registered, a version that has not been published,
  a column the dataset does not model.
- `ALREADY_EXISTS` (409): a dataset or relation name is taken.
- `CONFLICT` (409): the dataset moved past the version the caller edited.
- `VALIDATION_ERROR` (422): the body is malformed before any service runs.

**Versions.** A version is created by an import or by a maintenance action, and
never edited. Reads default to the current version; `version` / `as_of_version`
selects another, and a comparison names both.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.api.deps import Auth, DatasetSvc, require
from app.api.public_api import PUBLIC
from app.core.permissions import Perm
from app.schemas.kb_dataset import (
    DatasetBaseCreate,
    DatasetCompareResult,
    DatasetCreate,
    DatasetFieldChange,
    DatasetImportAccepted,
    DatasetImportCommit,
    DatasetImportDraft,
    DatasetImportPlan,
    DatasetImportRead,
    DatasetImportStatus,
    DatasetList,
    DatasetOperationList,
    DatasetQuery,
    DatasetQueryResult,
    DatasetRead,
    DatasetRelationInput,
    DatasetRelationList,
    DatasetRowList,
    DatasetRowRestore,
    DatasetRowUpdate,
    DatasetUpdate,
    DatasetVersionList,
)

router = APIRouter(dependencies=[PUBLIC])


@router.post(
    "/kb/datasets/bases",
    status_code=201,
    dependencies=[Depends(require(Perm.DATASETS_EDIT))],
)
async def create_dataset_base(data: DatasetBaseCreate, service: DatasetSvc, ctx: Auth) -> Any:
    """A dataset knowledge base needs no embedding: it is rows, not passages."""
    base = await service.create_base(data, ctx)
    return {
        "id": base.id,
        "name": base.name,
        "description": base.description,
        "scope": base.scope,
        "kb_type": base.kb_type,
    }


@router.get(
    "/kb/{kb_id}/datasets",
    response_model=DatasetList,
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def list_datasets(kb_id: UUID, service: DatasetSvc, ctx: Auth) -> Any:
    items = await service.list_datasets(kb_id, ctx)
    return {"items": items, "total": len(items)}


@router.post(
    "/kb/{kb_id}/datasets",
    response_model=DatasetRead,
    status_code=201,
    dependencies=[Depends(require(Perm.DATASETS_EDIT))],
)
async def create_dataset(kb_id: UUID, data: DatasetCreate, service: DatasetSvc, ctx: Auth) -> Any:
    """Agree the model before any row exists.

    `fields` and the primary key are the contract the importer and the query
    compiler both read; creating a dataset without them is what this route
    exists to prevent.
    """
    if data.knowledge_base_id != kb_id:
        data.knowledge_base_id = kb_id
    return await service.create(kb_id, data, ctx)


@router.get(
    "/kb/datasets/{dataset_id}",
    response_model=DatasetRead,
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def get_dataset(dataset_id: UUID, service: DatasetSvc, ctx: Auth) -> Any:
    return await service.get_dataset(dataset_id, ctx)


@router.patch(
    "/kb/datasets/{dataset_id}",
    response_model=DatasetRead,
    dependencies=[Depends(require(Perm.DATASETS_EDIT))],
)
async def update_dataset(
    dataset_id: UUID, data: DatasetUpdate, service: DatasetSvc, ctx: Auth
) -> Any:
    return await service.rename(dataset_id, data, ctx)


@router.delete(
    "/kb/datasets/{dataset_id}",
    status_code=204,
    dependencies=[Depends(require(Perm.DATASETS_EDIT))],
)
async def archive_dataset(dataset_id: UUID, service: DatasetSvc, ctx: Auth) -> None:
    """Archive a dataset. Its versions stay readable; it stops being queried."""
    await service.archive(dataset_id, ctx)


@router.put(
    "/kb/datasets/{dataset_id}/model",
    response_model=DatasetRead,
    dependencies=[Depends(require(Perm.DATASETS_EDIT))],
)
async def update_model(
    dataset_id: UUID, data: DatasetFieldChange, service: DatasetSvc, ctx: Auth
) -> Any:
    """Replace the field list, bumping the model version once."""
    return await service.update_model(dataset_id, data, ctx)


@router.get(
    "/kb/datasets/{dataset_id}/rows",
    response_model=DatasetRowList,
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def list_rows(
    dataset_id: UUID,
    service: DatasetSvc,
    ctx: Auth,
    version: int | None = Query(None, ge=1, description="Read the rows as of this version"),
    search: str | None = Query(None, max_length=200),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Any:
    return await service.list_rows(
        dataset_id, ctx, version_no=version, search=search, skip=skip, limit=limit
    )


@router.get(
    "/kb/datasets/{dataset_id}/rows/{record_key}/snapshot",
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def row_snapshot(
    dataset_id: UUID,
    record_key: str,
    service: DatasetSvc,
    ctx: Auth,
    version: int = Query(..., ge=1),
) -> Any:
    """What one record said at a version - the view behind an old citation."""
    return await service.row_snapshot(dataset_id, ctx, record_key=record_key, version_no=version)


@router.post(
    "/kb/datasets/{dataset_id}/rows",
    status_code=201,
    dependencies=[Depends(require(Perm.DATASETS_MAINTAIN))],
)
async def insert_row(
    dataset_id: UUID, data: DatasetRowUpdate, service: DatasetSvc, ctx: Auth
) -> Any:
    return await service.insert_row(
        dataset_id,
        ctx,
        values=data.values,
        reason=data.reason,
        expected_version=data.expected_version,
    )


@router.patch(
    "/kb/datasets/{dataset_id}/rows/{record_key}",
    dependencies=[Depends(require(Perm.DATASETS_MAINTAIN))],
)
async def update_row(
    dataset_id: UUID,
    record_key: str,
    data: DatasetRowUpdate,
    service: DatasetSvc,
    ctx: Auth,
) -> Any:
    return await service.update_row(
        dataset_id,
        ctx,
        record_key=record_key,
        values=data.values,
        reason=data.reason,
        expected_version=data.expected_version,
    )


@router.delete(
    "/kb/datasets/{dataset_id}/rows/{record_key}",
    dependencies=[Depends(require(Perm.DATASETS_MAINTAIN))],
)
async def delete_row(
    dataset_id: UUID,
    record_key: str,
    service: DatasetSvc,
    ctx: Auth,
    reason: str = Query(..., min_length=3, max_length=500),
    expected_version: int | None = Query(None, ge=1),
) -> Any:
    """Remove a record from every version going forward.

    It stops appearing in new answers and in the current rows; versions before
    the deletion still read it, and the deletion itself is a tombstone that says
    the record was removed rather than lost.
    """
    return await service.delete_row(
        dataset_id,
        ctx,
        record_key=record_key,
        reason=reason,
        expected_version=expected_version,
    )


@router.post(
    "/kb/datasets/{dataset_id}/rows/{record_key}/restore",
    dependencies=[Depends(require(Perm.DATASETS_MAINTAIN))],
)
async def restore_row(
    dataset_id: UUID,
    record_key: str,
    data: DatasetRowRestore,
    service: DatasetSvc,
    ctx: Auth,
) -> Any:
    return await service.restore_row(
        dataset_id,
        ctx,
        record_key=record_key,
        reason=data.reason,
        expected_version=None,
    )


@router.get(
    "/kb/datasets/{dataset_id}/operations",
    response_model=DatasetOperationList,
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def list_operations(
    dataset_id: UUID,
    service: DatasetSvc,
    ctx: Auth,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Any:
    return await service.operations(dataset_id, ctx, skip=skip, limit=limit)


@router.get(
    "/kb/datasets/{dataset_id}/versions",
    response_model=DatasetVersionList,
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def list_versions(
    dataset_id: UUID,
    service: DatasetSvc,
    ctx: Auth,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Any:
    return await service.versions(dataset_id, ctx, skip=skip, limit=limit)


@router.get(
    "/kb/datasets/{dataset_id}/versions/{version_no}/model",
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def version_model(dataset_id: UUID, version_no: int, service: DatasetSvc, ctx: Auth) -> Any:
    return await service.version_snapshot(dataset_id, ctx, version_no)


@router.get(
    "/kb/datasets/{dataset_id}/compare",
    response_model=DatasetCompareResult,
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def compare_versions(
    dataset_id: UUID,
    from_version: int,
    to_version: int,
    service: DatasetSvc,
    ctx: Auth,
    limit: int = Query(200, ge=1, le=2000),
) -> Any:
    return await service.compare(
        dataset_id, ctx, from_version=from_version, to_version=to_version, limit=limit
    )


@router.post(
    "/kb/datasets/{dataset_id}/query",
    response_model=DatasetQueryResult,
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def run_dataset_query(
    dataset_id: UUID, data: DatasetQuery, service: DatasetSvc, ctx: Auth
) -> Any:
    """Ask a question in the DSL: fields, filters, group by, windows, joins.

    The compiler resolves every name against the stored model and binds every
    value; nothing in the body becomes SQL text.
    """
    return await service.query(dataset_id, ctx, data)


@router.get(
    "/kb/datasets/{dataset_id}/relations",
    response_model=DatasetRelationList,
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def list_relations(dataset_id: UUID, service: DatasetSvc, ctx: Auth) -> Any:
    items = await service.relations(dataset_id, ctx)
    return {"items": items, "total": len(items)}


@router.post(
    "/kb/datasets/{dataset_id}/relations",
    status_code=201,
    dependencies=[Depends(require(Perm.DATASETS_EDIT))],
)
async def create_relation(
    dataset_id: UUID, data: DatasetRelationInput, service: DatasetSvc, ctx: Auth
) -> Any:
    relation, warnings = await service.create_relation(dataset_id, ctx, data)
    return {"relation": relation, "warnings": warnings}


@router.delete(
    "/kb/datasets/{dataset_id}/relations/{relation_id}",
    status_code=204,
    dependencies=[Depends(require(Perm.DATASETS_EDIT))],
)
async def delete_relation(
    dataset_id: UUID, relation_id: UUID, service: DatasetSvc, ctx: Auth
) -> None:
    await service.delete_relation(dataset_id, relation_id, ctx)


@router.get(
    "/kb/datasets/{dataset_id}/relation-candidates",
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def relation_candidates(dataset_id: UUID, service: DatasetSvc, ctx: Auth) -> Any:
    """Suggested joins to sibling datasets, scored on names and value overlap."""
    items = await service.relation_candidates(dataset_id, ctx)
    return {"items": items, "total": len(items)}


@router.post(
    "/kb/{kb_id}/datasets/imports",
    response_model=DatasetImportRead,
    status_code=201,
    dependencies=[Depends(require(Perm.DATASETS_IMPORT))],
)
async def preview_import(
    kb_id: UUID,
    service: DatasetSvc,
    ctx: Auth,
    file: Annotated[UploadFile, File(description="An .xlsx, .xlsm or .xls workbook")],
) -> Any:
    """Parse a workbook and propose datasets; writes no rows.

    The response names every sheet, its header row, the inferred types and a
    suggested primary key, plus what the parser was unsure about.
    """
    content = await file.read()
    record = await service.preview_import(
        kb_id, ctx, filename=file.filename or "workbook.xlsx", content=content
    )
    return {
        "id": record.id,
        "knowledge_base_id": record.knowledge_base_id,
        "status": record.status,
        "filename": record.filename,
        "file_size": record.file_size,
        "sheet_count": record.sheet_count,
        "sheets": (record.plan or {}).get("sheets", []),
        "summary": record.summary,
        "error": record.error,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "committed_at": record.committed_at,
    }


@router.get(
    "/kb/{kb_id}/datasets/imports",
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def list_imports(
    kb_id: UUID,
    service: DatasetSvc,
    ctx: Auth,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> Any:
    return await service.imports(kb_id, ctx, skip=skip, limit=limit)


@router.post(
    "/kb/{kb_id}/datasets/imports/{import_id}/commit",
    response_model=DatasetImportAccepted,
    status_code=202,
    dependencies=[Depends(require(Perm.DATASETS_IMPORT))],
)
async def commit_import(
    kb_id: UUID,
    import_id: UUID,
    data: DatasetImportCommit,
    service: DatasetSvc,
    ctx: Auth,
) -> Any:
    """Accept the chosen sheets and publish them on a worker.

    Answers before the workbook is read: a 40 MB file takes minutes, and a
    request that holds a connection for it is a timeout waiting to happen - one
    that also lost the whole import when the process went away mid-way. Poll
    `GET .../imports/{id}` for the progress of each sheet; the queued row's
    `summary` is written as each one lands.
    """
    return await service.queue_import(kb_id, import_id, ctx, data)


@router.get(
    "/kb/{kb_id}/datasets/imports/{import_id}",
    response_model=DatasetImportStatus,
    dependencies=[Depends(require(Perm.DATASETS_VIEW))],
)
async def import_status(kb_id: UUID, import_id: UUID, service: DatasetSvc, ctx: Auth) -> Any:
    """How far a publish got: `queued`, `running` with the sheet in flight, or done.

    The sheets that have landed are in `sheets`, so a partial import can be read
    without downloading the workbook again. `plan` carries the parsed workbook
    and the decisions saved against it, which is what the wizard resumes from.
    """
    return await service.import_status(kb_id, import_id, ctx)


@router.put(
    "/kb/{kb_id}/datasets/imports/{import_id}",
    response_model=DatasetImportPlan,
    dependencies=[Depends(require(Perm.DATASETS_IMPORT))],
)
async def save_import_draft(
    kb_id: UUID, import_id: UUID, data: DatasetImportDraft, service: DatasetSvc, ctx: Auth
) -> Any:
    """Save the wizard's decisions and the step reached; publish nothing.

    A dialog is a place people leave halfway. Without this, closing it at step 3
    of 6 means uploading the workbook again and repeating every choice.
    """
    return await service.save_import_draft(kb_id, import_id, ctx, data)
