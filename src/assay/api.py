from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import (
    DataSetImportViaPathRequest,
    DataSetImportViaPathResponse,
    DataSetInfo,
    ZTestRequest,
    ZTestResult,
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
    from assay.services import run_z_test

    return await run_z_test(request, session)


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
async def upload_dataset(request: DataSetImportViaPathRequest, session: SessionDep): # pragma: no cover  # noqa: E501
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
    from assay.services import upload_dataset_via_path

    return await upload_dataset_via_path(request, session)


@router.post(
    path="/upload-dataset/update-dataset-name",
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
    from assay.services import update_dataset_name as _update_dataset_name

    return await _update_dataset_name(request, session)
