from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import TestPlanName
from assay.schemas.test_plans import TestPlanCreationResponse
from assay.services import create_new_test_plan

router = APIRouter(tags=["test plan"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
