import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import JudgeCheckModel
from assay.schemas import JudgeCheck
from assay.services.settings._common import _judge_check_schema


async def get_judge_check(check_id: uuid.UUID, session: AsyncSession) -> JudgeCheck:
    """One check of the judge settings, as far as it has got — what the UI
    polls until `status` is `completed`.

    Args:
        check_id: UUID of the check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if no check with this ID exists.
    """
    check = await session.get(JudgeCheckModel, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail=f"Check with ID '{check_id}' not found")
    return _judge_check_schema(check)
