from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import TestSetCreationResponse, TestSetName
from assay.services import create_new_test_set

router = APIRouter(tags=["test set"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
