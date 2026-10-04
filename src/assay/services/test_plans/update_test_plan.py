import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.schemas import ModifyTestPlanRequest, TestPlanMetadata, TestPlanName
from assay.services.test_plans._common import (
    _check_unique_test_plan_name_or_409,
    _describe_test_plans,
    _find_test_plan_by_id_or_404,
)

logger = logging.getLogger(__name__)


async def update_test_plan_by_id(
        test_plan_id: uuid.UUID,
        request: ModifyTestPlanRequest,
        session: AsyncSession,
) -> TestPlanMetadata:
    """Orchestrates test plan rename: fetches, validates uniqueness, persists, and
    returns the updated metadata.

    The uniqueness check only runs when the requested name actually differs from
    the plan's current name, so re-submitting the plan's own unchanged name is a
    safe no-op rather than a false 409.

    Args:
        test_plan_id: UUID of the test plan to update.
        request: Request containing the new name (omit or set to `null` to leave
            it unchanged).
        session: Active async database session.

    Returns:
        The updated test plan metadata (id, name, created_at).

    Raises:
        HTTPException: 404 if no test plan with the given ID exists.
        HTTPException: 409 if another test plan already has the requested name.
    """
    found = await _find_test_plan_by_id_or_404(test_plan_id, session)

    previous_name = found.name
    renamed = request.name != previous_name and request.name is not None
    if renamed:
        await _check_unique_test_plan_name_or_409(
            TestPlanName(name=request.name), session
        )
        found.name = request.name

    await session.commit()

    if renamed:
        logger.info(
            "Renamed test plan %s from %r to %r", test_plan_id, previous_name, request.name,
            extra={"test_plan_id": test_plan_id},
        )

    (described,) = await _describe_test_plans([found], session)
    return described
