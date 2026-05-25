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
    DataSetRowToUpdate,
    DataSetRowUpdatedData,
    PaginatedDataSetResponse,
    ZTestRequest,
    ZTestResult,
)
from assay.services import (
    delete_dataset_by_id,
    delete_dataset_rows_by_ids,
    get_datasets_metadata,
    replace_dataset_content_by_dataset_id,
    run_z_test,
    update_dataset_name_by_id,
    update_dataset_rows_by_id,
    upload_dataset_via_path,
    upload_new_rows_in_existing_dataset,
)

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    path="/statistical-tests/z-test",
    tags=["analysis"],
    response_model=ZTestResult,
)
async def z_test(request: ZTestRequest, session: SessionDep) -> ZTestResult: # pragma: no cover
    """One-sample z-test for metric score distributions.

    Tests whether the population mean of `scores` is statistically different from
    `threshold` at the given significance level (`alpha`).

    Typical use: pass 100 ROUGE scores and a minimum quality threshold — the
    response tells you whether the difference is statistically significant or
    could be due to chance.

    **Interpretation**
    - `passed: true` — reject H0; sufficient evidence the true mean clears the threshold.
    - `p_value` — probability of observing this result if H0 were true; lower is stronger evidence.
    - `confidence_interval` — two-sided (1 - alpha) CI for the true population mean.

    **Note:** reliable for n ≥ 30. For smaller samples the t-distribution would be more appropriate.
    """

    return await run_z_test(request, session)


@router.get(
    path="/datasets",
    responses={},
    tags=["dataset"],
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


@router.post(
    path="/upload-dataset/path",
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
    tags=["dataset"],
    response_model=DataSetImportViaPathResponse,
)
async def upload_dataset(
        request: DataSetImportViaPathRequest,
        session: SessionDep): # pragma: no cover
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
    path="/upload-dataset/upload-dataset-rows",
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
    tags=["dataset"],
    response_model=DataSetImportedData,
)
async def upload_dataset_rows(
        request: DataSetImportingData,
        session: SessionDep) -> DataSetImportedData:  # pragma: no cover
    """Add rows to an existing dataset.

    The request body must include the dataset `id` and a list of `rows`,
    each with `prompt`, `expected_output`, and `model_output`.
    The dataset must already exist — use the upload-via-path endpoint to create one.
    """

    return await upload_new_rows_in_existing_dataset(request, session)


@router.patch(
    path="/update-dataset/name",
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
    tags=["dataset"],
    response_model=DataSetInfo,
)
async def update_dataset_name(request: DataSetInfo, session: SessionDep): # pragma: no cover
    """Rename a dataset by its ID.

    The request body must include both the dataset `id` and the desired `name`.
    The new name must be unique across all datasets. If the name is unchanged,
    the request succeeds without a uniqueness check.
    """

    return await update_dataset_name_by_id(request, session)


@router.patch(
    path="/update-dataset/rows",
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
    tags=["dataset"],
    response_model=DataSetRowUpdatedData,
)
async def update_dataset_rows(
        request: list[DataSetRowToUpdate],
        session: SessionDep) -> DataSetRowUpdatedData:  # pragma: no cover
    """Update existing dataset rows by their IDs.

    The request body must be a list of objects, each with a row `id` and a `row_info`
    containing `prompt`, `expected_output`, and `model_output`.
    If any row ID is not found, the entire request is rejected with a 404.
    """

    return await update_dataset_rows_by_id(request, session)


@router.put(
    path="/update-dataset",
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
    tags=["dataset"],
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
    path="/delete-dataset",
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
    tags=["dataset"],
    response_model=DataSetDeletedData,
)
async def delete_dataset(request: DataSetID, session: SessionDep):  # pragma: no cover
    """Delete a dataset by its ID.

    The request body must include the dataset `id`.
    All rows associated with the dataset are also deleted.
    """

    return await delete_dataset_by_id(request, session)


@router.delete(
    path="/delete-dataset/delete-dataset-rows",
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
    tags=["dataset"],
    response_model=DataSetDeletedData,
)
async def delete_dataset_rows(  # pragma: no cover
        request: DataSetDeletingData, session: SessionDep):
    """Delete rows from an existing dataset by their IDs.

    The request body must include a list of `row_ids` to delete.
    If any ID is not found, the entire request is rejected with a 404 — no rows are deleted.
    """

    return await delete_dataset_rows_by_ids(request, session)
