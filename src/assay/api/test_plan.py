import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import (
    PaginatedTestPlanMetadataResponse,
    TestPlanCreationResponse,
    TestPlanMetadata,
    TestPlanName,
)
from assay.services import (
    create_new_test_plan,
    get_all_test_plans_metadata,
    get_test_plan_metadata_by_id,
)

router = APIRouter(tags=["test plan"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    path="/test-plans/{test_plan_id}",
    responses={
        200: {
            "description": "Metadata for the requested test plan.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "Regression plan",
                        "created_at": "2026-07-03T15:43:09.032480",
                    }
                }
            },
        },
        404: {
            "description": "No test plan exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test plan with ID '<test_plan_id>' not found"
                    }
                }
            },
        },
    },
    response_model=TestPlanMetadata,
)
async def get_single_test_plan_metadata(
        test_plan_id: uuid.UUID,
        session: SessionDep,
) -> TestPlanMetadata: # pragma: no cover
    """Retrieve metadata for a single test plan.

    Returns the test plan's `id`, `name`, and `created_at` timestamp.
    """
    return await get_test_plan_metadata_by_id(test_plan_id, session)


@router.get(
    path="/test-plans",
    responses={
        200: {
            "description": "A paginated list of test plan metadata.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                "name": "Regression plan",
                                "created_at": "2026-07-03T15:43:09.032480",
                            },
                            {
                                "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                                "name": "Smoke plan",
                                "created_at": "2026-07-03T16:00:00.000000",
                            },
                        ],
                    }
                }
            },
        },
    },
    response_model=PaginatedTestPlanMetadataResponse,
)
async def get_test_plans_metadata(
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(default=100, description="Maximum number of records to "
                                                    "return for pagination.")
) -> PaginatedTestPlanMetadataResponse: # pragma: no cover
    """List all test plans with their metadata, paginated.

    Returns each test plan's `id`, `name`, and `created_at` timestamp.
    Use `offset` and `limit` to page through results. The response includes `total`
    so the client can calculate the number of pages.
    """
    return await get_all_test_plans_metadata(session, offset, limit)


@router.post(
    path="/test-plans",
    responses={
        200: {
            "description": "Test plan created successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "My test plan",
                    }
                }
            },
        },
        409: {
            "description": "A test plan with the given name already exists.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test plan with name 'My test plan' already exists"
                    }
                }
            },
        },
    },
    response_model=TestPlanCreationResponse,
)
async def create_test_plan(
        request: TestPlanName,
        session: SessionDep,
) -> TestPlanCreationResponse: # pragma: no cover
    """Create a new test plan.

    Test plan names must be unique — a 409 is returned if the name is already taken.

    Returns the created test plan with its generated ID and name.
    """
    return await create_new_test_plan(request, session)
