from fastapi import APIRouter
from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.annotation import Annotated

from assay.db import get_session

router = APIRouter(tags=["test"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    path="/tests",
    responses={}
)
async def create_test_manually():
    ...
