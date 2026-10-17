# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from assay.api._filters import CreatedEdges, CreatedFrom, CreatedTo, Limit, Offset, search
from assay.db import get_session
from assay.schemas import (
    DataSetDeletedData,
    DataSetDeletingData,
    DatasetFacets,
    DataSetID,
    DataSetImportedData,
    DataSetImportingData,
    DataSetInfo,
    DataSetMetadata,
    DataSetRow,
    DataSetRowUpdatedData,
    DatasetSort,
    DataSetUploadLimits,
    DataSetUploadRefused,
    PaginatedDataSetResponse,
    PaginatedDataSetRowResponse,
    RowRange,
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
    upload_dataset,
    upload_limits,
    upload_new_rows_in_existing_dataset,
)
from assay.services.datasets.get_datasets_metadata import DatasetFilters, get_dataset_facets

router = APIRouter(tags=["dataset"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def dataset_filters(
        made_into_tests: Annotated[bool | None, Query(
            description="Only datasets some tests were (true) or no test was (false) made "
                        "from.")] = None,
        rows: Annotated[list[RowRange] | None, Query(
            description="Only datasets of one of these sizes, by their rows: `0`, `1-10`, "
                        "`11-100`, `101+` (the `rows` facet's keys). Repeatable.")] = None,
        created_from: CreatedFrom = None,
        created_to: CreatedTo = None,
        q: Annotated[list[str] | None, search("the dataset's name and first prompt")] = None,
) -> DatasetFilters:
    return DatasetFilters(made_into_tests=made_into_tests, rows=rows,
                          created_from=created_from, created_to=created_to, q=q)


@router.get(
    path="/datasets/facets",
    summary="Count the datasets within the filters by each filter's values",
    responses={200: {"content": {"application/json": {"example": {
        "made_into_tests": {"true": 2, "false": 1},
        "rows": {"0": 0, "1-10": 2, "11-100": 1, "101+": 0},
        "created": None,
    }}}}},
    response_model=DatasetFacets,
)
async def get_datasets_facets(
        session: SessionDep,
        filters: Annotated[DatasetFilters, Depends(dataset_filters)],
        created_edges: CreatedEdges = None,
) -> DatasetFacets:  # pragma: no cover
    """The datasets `GET /datasets` would list with the same filters, counted by
    each filter's values: each facet within every other filter chosen. `rows`
    counts by row count: `0`, `1-10`, `11-100`, `101+`."""
    return await get_dataset_facets(session, filters, created_edges)


_DETAIL_SCHEMA = {
    "type": "object",
    "properties": {"detail": {"type": "string"}},
    "required": ["detail"],
}


@router.get(
    path="/datasets/upload",
    summary="The largest file a dataset upload accepts",
    responses={200: {"content": {"application/json": {"example": {"max_bytes": 10485760}}}}},
    response_model=DataSetUploadLimits,
)
async def get_dataset_upload_limits() -> DataSetUploadLimits:  # pragma: no cover
    """How large a file `POST /datasets/upload` accepts, in bytes, so a client can say so and
    refuse a larger file before sending it. Set by `ASSAY_DATASET_UPLOAD_MAX_MB` (10 MB by
    default)."""
    return upload_limits()


@router.post(
    path="/datasets/upload",
    status_code=201,
    summary="Create a dataset from an uploaded .jsonl file",
    responses={
        201: {"content": {"application/json": {"example": {
            "dataset": {"id": "6f1c2a9e-8b7d-4c3e-9a1f-2d5e8c7b4a10", "name": "Support, English"},
            "loaded": {"n": 2, "ids": ["0b5d3f7e-1c2a-4e9b-8d6f-3a7c9e1b5d20",
                                       "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c30"]},
        }}}},
        409: {
            "description": "A dataset with this name already exists; the file wasn't read.",
            "content": {"application/json": {
                "example": {"detail": "Dataset name 'Support, English' already exists."},
                "schema": _DETAIL_SCHEMA,
            }},
        },
        413: {
            "description": "The file is larger than the limit `GET /datasets/upload` gives.",
            "content": {"application/json": {
                "example": {"detail": "The file is larger than 10 MB."},
                "schema": _DETAIL_SCHEMA,
            }},
        },
        422: {
            "model": DataSetUploadRefused,
            "description": "Nothing was imported. `detail` is an object when lines are "
                           "invalid, a string when the file isn't UTF-8 text, and FastAPI's "
                           "usual list when `file` or `dataset_name` is missing or invalid.",
            "content": {"application/json": {"examples": {
                "invalid_lines": {"summary": "Lines that aren't valid rows", "value": {
                    "detail": {
                        "msg": "2 lines aren't valid dataset rows. Nothing was imported.",
                        "invalid_lines": 2,
                        "lines": [
                            {"line": 3, "msg": "expected_output is missing.", "errors": [
                                {"loc": ["expected_output"], "msg": "Field required",
                                 "type": "missing"}]},
                            {"line": 7, "msg": "Not valid JSON (near character 2).",
                             "errors": [{"loc": [],
                                         "msg": "Invalid JSON: expected ident at line 1 "
                                                "column 2",
                                         "type": "json_invalid"}]},
                        ],
                    }}},
                "not_utf8": {"summary": "A file that isn't UTF-8 text",
                             "value": {"detail": "The file isn't UTF-8 text."}},
                "missing_part": {"summary": "A part missing", "value": {"detail": [
                    {"type": "missing", "loc": ["body", "dataset_name"],
                     "msg": "Field required"}]}},
            }}},
        },
    },
    response_model=DataSetImportedData,
)
async def create_dataset_from_upload(
        file: Annotated[UploadFile, File(
            description="The `.jsonl` file: UTF-8 text, one JSON object per line with "
                        "`prompt`, `expected_output` and `model_output`, all strings. Blank "
                        "lines are skipped; the file's name doesn't matter.")],
        dataset_name: Annotated[str, Form(
            min_length=1, max_length=255,
            description="The new dataset's name, unique among datasets.")],
        session: SessionDep) -> DataSetImportedData:  # pragma: no cover
    """Create a dataset from an uploaded `.jsonl` file, one row per non-blank line, in the
    file's order.

    Every line is checked before anything is written: one invalid line and nothing is
    imported, and the 422 says which lines and why (the first 100, counting them all). The
    name is checked first, before the file is read. A file with only blank lines creates an
    empty dataset.

    **Example line:**
    ```json
    {"prompt": "How do I reset my password?", "expected_output": "Go to Settings, then Security.",
     "model_output": "Open Settings and choose Security."}
    ```
    A blank `model_output` means no recorded answer: a test made from that row asks the
    application under test instead.
    """
    return await upload_dataset(file, dataset_name, session)


@router.get(
    path="/datasets",
    responses={},
    response_model=PaginatedDataSetResponse,
)
async def get_all_datasets_metadata(
        session: SessionDep,
        filters: Annotated[DatasetFilters, Depends(dataset_filters)],
        sort: Annotated[DatasetSort, Query(
            description="`newest` (the default), `oldest` or `name` (ignoring case).")
        ] = DatasetSort.newest,
        offset: Offset = 0,
        limit: Limit = 100,
) -> PaginatedDataSetResponse:  # pragma: no cover
    """List the datasets within the filters, sorted and paged, each with its rows
    counted and its first prompt. `total` counts every dataset within the
    filters."""
    return await get_datasets_metadata(offset, limit, session, filters, sort)


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
    offset: Offset = 0,
    limit: Limit = 100,
    q: Annotated[list[str] | None, search("the row's prompt, expected output and model "
                                           "output")] = None,
) -> PaginatedDataSetRowResponse:  # pragma: no cover
    """List a dataset's rows by number, each with its number and how many tests
    were made from it; `q` narrows them. `total` counts the rows within `q`."""
    return await get_dataset_rows_by_id(dataset_id, session, offset, limit, q)


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
