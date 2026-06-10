from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.models import TestModel
from assay.schemas.tests import CreateTestCaseRequest, CreateTestCaseResponse

router = APIRouter(tags=["test"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    path="/tests",
    responses={},
    # response_model=CreateTestCaseResponse,
)
async def create_test_manually(
        request: CreateTestCaseRequest, 
        session: SessionDep): # pragma: no cover

    session.add(
        TestModel(
            name=request.name,
            input=request.input,
            model_output=request.model_output,
            expected_output=request.expected_output,
        )
    )
    await session.commit()
    ...
