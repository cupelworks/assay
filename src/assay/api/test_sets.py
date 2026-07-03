from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import PaginatedTestSetMetadataResponse, TestSetCreationResponse, TestSetName
from assay.services import create_new_test_set, get_all_test_sets_metadata

router = APIRouter(tags=["test set"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    path="/test-sets",
    responses={
        200: {
            "description": "A paginated list of test set metadata.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                "name": "Regression suite",
                                "created_at": "2026-07-03T15:43:09.032480",
                            },
                            {
                                "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                                "name": "Smoke tests",
                                "created_at": "2026-07-03T16:00:00.000000",
                            },
                        ],
                    }
                }
            },
        },
    },
    response_model=PaginatedTestSetMetadataResponse
)
async def get_test_sets_metadata(
    session: SessionDep,
    offset: int = Query(default=0, description="Number of records to skip for pagination."),
    limit: int = Query(
        default=100, description="Maximum number of records to return for pagination."
    ),
) -> PaginatedTestSetMetadataResponse: # pragma: no cover
    """List all test sets with their metadata, paginated.

    Returns each test set's `id`, `name`, and `created_at` timestamp.
    Use `offset` and `limit` to page through results. The response includes `total`
    so the client can calculate the number of pages.
    """
    return await get_all_test_sets_metadata(
        session, offset, limit
    )


@router.post(
    path="/test-sets",
    responses={
        200: {
            "description": "Test set created successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "My test set",
                    }
                }
            },
        },
        409: {
            "description": "A test set with the given name already exists.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test set with name 'My test set' already exists"
                    }
                }
            },
        },
    },
    response_model=TestSetCreationResponse,
)
async def create_test_set(
        request: TestSetName,
        session: SessionDep
) -> TestSetCreationResponse: # pragma: no cover
    """Create a new test set.

    Test set names must be unique — a 409 is returned if the name is already taken.

    Returns the created test set with its generated ID and name.
    """
    return await create_new_test_set(request, session)
