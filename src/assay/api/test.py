from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import CreateTestCaseFromDatasetResponse, DataSetID
from assay.schemas.tests import (
    CreateTestCaseFromDatasetRequest,
    CreateTestCaseRequest,
    CreateTestCaseResponse,
)
from assay.services import create_new_test
from assay.services.tests import create_new_test_from_dataset

router = APIRouter(tags=["test"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    path=".tests",
    responses={
        200: {
            "description": "Test case created successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "My test case",
                        "input": "Summarise this article in one sentence.",
                        "expected_output": "A concise one-sentence summary.",
                        "model_output": None,
                        "test_type_names": ["ROUGE", "BERTScore"],
                    }
                }
            },
        },
        422: {
            "description": "One or more test type names are not in the catalogue.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Unknown test types: {'Invalid Type'}"
                    }
                }
            },
        },
    },
    response_model=CreateTestCaseResponse,
)
async def create_test_manually(
        request: CreateTestCaseRequest,
        session: SessionDep) -> CreateTestCaseResponse:  # pragma: no cover
    """Create a new test case manually.

    Persists a single test definition with its input and optional reference outputs.
    The test is immediately available for standalone execution or inclusion in a test set.

    `name` defaults to a fresh UUID if omitted.
    `expected_output` is required by test types that compare against a reference (e.g. NLP metrics,
    LLM-as-judge). Leave it `null` for deterministic checks that do not need one.
    `model_output` can be pre-populated if the model response is already known;
    otherwise leave it `null` and it will be filled in when the test is run.
    `test_type_names` is an optional list of evaluation strategies to assign. Each name must exist
    in the test types catalogue — a 422 is returned if any name is unrecognized.

    On success, returns the created test case with its generated `id` and all input fields.
    """

    return await create_new_test(request, session)


@router.post(
    path="/tests/from-dataset",
    responses={
        200: {
            "description": "Test cases created successfully for every row in the dataset.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "test_cases": [
                            {"id": "11111111-1111-1111-1111-111111111111"},
                            {"id": "22222222-2222-2222-2222-222222222222"},
                        ],
                    }
                }
            },
        },
        404: {
            "description": "Dataset not found, or the dataset has no rows.",
            "content": {
                "application/json": {
                    "examples": {
                        "dataset_not_found": {
                            "summary": "Dataset not found",
                            "value": {
                                "detail": (
                                    "Dataset with id"
                                    " a1b2c3d4-e5f6-7890-abcd-ef1234567890 not found"
                                )
                            },
                        },
                        "no_rows": {
                            "summary": "Dataset has no rows",
                            "value": {
                                "detail": (
                                    "No Dataset Rows were found with dataset id"
                                    " a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                )
                            },
                        },
                    }
                }
            },
        },
        422: {
            "description": "One or more test type names are not in the catalogue.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Unknown test types: {'Invalid Type'}"
                    }
                }
            },
        },
    },
    response_model=CreateTestCaseFromDatasetResponse,
)
async def create_test_from_dataset(
        request: CreateTestCaseFromDatasetRequest,
        session: SessionDep) -> CreateTestCaseFromDatasetResponse:  # pragma: no cover
    """Create test cases in bulk from all rows of an existing dataset.

    Each row in the dataset becomes a separate test case. All created tests share
    the same optional list of evaluation strategies (`test_type_names`).

    `test_type_names` is an optional list of evaluation strategies to assign. Each name must exist
    in the test types catalogue — a 422 is returned if any name is unrecognized.

    Returns the dataset ID and the IDs of all created test cases.
    """

    return await create_new_test_from_dataset(request, session)
