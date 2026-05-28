import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import (
    DataSetDeletedData,
    DataSetDeletingData,
    DataSetID,
    DataSetImportedData,
    DataSetImportingData,
    DataSetImportViaPathRequest,
    DataSetImportViaPathResponse,
    DataSetInfo,
    DataSetMetadata,
    DataSetRow,
    DataSetRowUpdatedData,
    PaginatedDataSetResponse,
    PaginatedDataSetRowResponse,
)
from assay.services import (
    delete_dataset_by_id,
    delete_dataset_rows_by_ids,
    get_dataset_metadata_by_id,
    get_dataset_rows_by_id,
    get_datasets_metadata,
    replace_dataset_content_by_dataset_id,
    update_dataset_name_by_id,
    update_dataset_rows_by_id,
    upload_dataset_via_path,
    upload_new_rows_in_existing_dataset,
)

router = APIRouter(tags=["dataset"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    path="/datasets",
    responses={},
    response_model=PaginatedDataSetResponse,
)
async def get_all_datasets_metadata(
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(default=100, description="Maximum number of records to "
                                                    "return for pagination.")
) -> PaginatedDataSetResponse:  # pragma: no cover
    """List all datasets with their metadata, paginated.

    Use `offset` and `limit` to page through results. The response includes the total
    number of datasets so the client can calculate the number of pages.
    """

    return await get_datasets_metadata(offset, limit, session)


@router.get(
    path="/datasets/{dataset_id}",
    responses={
        404: {
            "description": "No dataset exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {"detail": "Dataset with id <example-id> not found"},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=DataSetMetadata,
)
async def get_dataset_metadata(
        dataset_id: uuid.UUID,
        session: SessionDep) -> DataSetMetadata:  # pragma: no cover
    """Retrieve metadata for a single dataset by its ID.

    Returns the dataset `id`, `name`, and `created_at` timestamp.
    """

    return await get_dataset_metadata_by_id(dataset_id, session)


@router.get(
    path="/datasets/{dataset_id}/rows",
    responses={
        404: {
            "description": "No dataset exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {"detail": "Dataset with id <example-id> not found"},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=PaginatedDataSetRowResponse,
)
async def get_dataset_rows(
    dataset_id: uuid.UUID,
    session: SessionDep,
    offset: int = Query(description="Number of records to skip for pagination."),
    limit: int = Query(description="Maximum number of records to return."),
) -> PaginatedDataSetRowResponse:  # pragma: no cover
    """List all rows in a dataset, paginated.

    Use `offset` and `limit` to page through results. The response includes the total
    number of rows so the client can calculate the number of pages.
    """

    return await get_dataset_rows_by_id(dataset_id, session, offset, limit)


@router.post(
    path="/datasets/path",
    responses={
        404: {
            "description": "File not found at the given path.",
            "content": {
                "application/json": {
                    "example": {"detail": "File not found: /path/to/file.jsonl"},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
        409: {
            "description": "A dataset with the same name already exists.",
            "content": {
                "application/json": {
                    "example": {"detail": "Dataset name 'my_dataset' already exists."},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
        422: {
            "description": "One or more lines in the .jsonl file failed schema validation.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "line": 2,
                                "content": '{"prompt": "missing model_output field"}',
                                "errors": [
                                    {
                                        "type": "missing",
                                        "loc": ["model_output"],
                                        "msg": "Field required",
                                    }
                                ],
                            }
                        ]
                    },
                }
            },
        },
    },
    response_model=DataSetImportViaPathResponse,
)
async def create_dataset_from_path(
        request: DataSetImportViaPathRequest,
        session: SessionDep):  # pragma: no cover
    """Load a dataset from a local `.jsonl` file and persist it as a Dataset with its rows.

    Each line must be a valid JSON object matching the dataset schema (`prompt`, `model_output`,
    and `expected_output`). All lines are validated before any data is written —
    the whole file is rejected if any line is invalid (fail fast, no partial imports).

    The dataset name must be unique — if a dataset with the same name already exists, the
    request is rejected before any file I/O is performed.

    On success, one `Dataset` record and one `DatasetRow` per line are created in a single
    transaction. The response includes the dataset ID, name, total row count, and the list
    of generated row IDs.

    **Example line:**
    ```json
    {"prompt": "Summarize this article...", "model_output": "Short summary.",
     "expected_output": "A brief summary."}
    ```

    **Note:** the file must be accessible from the server's filesystem.
    Remote URLs and cloud storage paths are not supported.
    """

    return await upload_dataset_via_path(request, session)


@router.post(
    path="/datasets/rows",
    responses={
        404: {
            "description": "No dataset exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {"detail": "Dataset with id <example-id> not found"},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=DataSetImportedData,
)
async def add_dataset_rows(
        request: DataSetImportingData,
        session: SessionDep) -> DataSetImportedData:  # pragma: no cover
    """Add rows to an existing dataset.

    The request body must include the dataset `id` and a list of `rows`,
    each with `prompt`, `expected_output`, and `model_output`.
    The dataset must already exist — use the upload-via-path endpoint to create one.
    """

    return await upload_new_rows_in_existing_dataset(request, session)


@router.patch(
    path="/datasets/name",
    responses={
        404: {
            "description": "No dataset exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {"detail": "Dataset with id <example-id> not found"},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
        409: {
            "description": "A dataset with the new name already exists.",
            "content": {
                "application/json": {
                    "example": {"detail": "Dataset name 'my_dataset' already exists."},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=DataSetInfo,
)
async def update_dataset_name(request: DataSetInfo, session: SessionDep):  # pragma: no cover
    """Rename a dataset by its ID.

    The request body must include both the dataset `id` and the desired `name`.
    The new name must be unique across all datasets. If the name is unchanged,
    the request succeeds without a uniqueness check.
    """

    return await update_dataset_name_by_id(request, session)


@router.patch(
    path="/datasets/rows",
    responses={
        404: {
            "description": "One or more row IDs were not found — no rows are updated.",
            "content": {
                "application/json": {
                    "example": {"detail": "Row ids not found: ['<example-id>']"},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=DataSetRowUpdatedData,
)
async def update_dataset_rows(
        request: list[DataSetRow],
        session: SessionDep) -> DataSetRowUpdatedData:  # pragma: no cover
    """Update existing dataset rows by their IDs.

    The request body must be a list of objects, each with a row `id` and a `row_info`
    containing `prompt`, `expected_output`, and `model_output`.
    If any row ID is not found, the entire request is rejected with a 404.
    """

    return await update_dataset_rows_by_id(request, session)


@router.put(
    path="/datasets/rows",
    responses={
        404: {
            "description": "No dataset exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {"detail": "Dataset with id <example-id> not found"},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=DataSetImportedData,
)
async def replace_dataset_content(
        request: DataSetImportingData,
        session: SessionDep) -> DataSetImportedData:  # pragma: no cover
    """Replace all rows in an existing dataset with a new set of rows.

    The request body must include the dataset `id` and a list of `rows`,
    each with `prompt`, `expected_output`, and `model_output`.
    All existing rows are deleted before the new ones are inserted — the operation
    is transactional; either all rows are replaced or none are.
    """

    return await replace_dataset_content_by_dataset_id(request, session)


@router.delete(
    path="/datasets",
    responses={
        404: {
            "description": "No dataset exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {"detail": "Dataset with id <example-id> not found"},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=DataSetDeletedData,
)
async def delete_dataset(request: DataSetID, session: SessionDep):  # pragma: no cover
    """Delete a dataset by its ID.

    The request body must include the dataset `id`.
    All rows associated with the dataset are also deleted.
    """

    return await delete_dataset_by_id(request, session)


@router.delete(
    path="/datasets/rows",
    responses={
        404: {
            "description": "One or more row IDs were not found.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Row ids not found: ['f6b155e0-5807-43ec-b240-8a4c5cb1c778']"
                    },
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=DataSetDeletedData,
)
async def delete_dataset_rows(  # pragma: no cover
        request: DataSetDeletingData, session: SessionDep):
    """Delete rows from an existing dataset by their IDs.

    The request body must include a list of `row_ids` to delete.
    If any ID is not found, the entire request is rejected with a 404 — no rows are deleted.
    """

    return await delete_dataset_rows_by_ids(request, session)
