import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TargetCheckModel
from assay.schemas import TargetCheck
from assay.services.settings._common import _target_check_schema


async def get_target_check(check_id: uuid.UUID, session: AsyncSession) -> TargetCheck:
    """One check of the application-under-test settings, as far as it has
    got — what the UI polls until `status` is `completed`.

    Args:
        check_id: UUID of the check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if no check with this ID exists.
    """
    check = await session.get(TargetCheckModel, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail=f"Check with ID '{check_id}' not found")
    return _target_check_schema(check)
