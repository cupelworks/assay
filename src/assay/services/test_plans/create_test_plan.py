# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestPlanModel
from assay.schemas import TestPlanName
from assay.schemas.test_plans import TestPlanCreationResponse
from assay.services.test_plans._common import _check_unique_test_plan_name_or_409

logger = logging.getLogger(__name__)


async def create_new_test_plan(
        request: TestPlanName,
        session: AsyncSession,
) -> TestPlanCreationResponse:
    """Create a new test plan with a unique name.

    Args:
        request: Request containing the test plan name.
        session: Active async database session.

    Returns:
        The created test plan with its generated ID and name.

    Raises:
        HTTPException: 409 if a test plan with the given name already exists.
    """
    await _check_unique_test_plan_name_or_409(request, session)

    new_test_plan_id = uuid.uuid4()
    test_plan_model = TestPlanModel(
        id=new_test_plan_id,
        name=request.name,
    )

    session.add(test_plan_model)
    await session.commit()

    logger.info(
        "Created test plan %s (%r)", new_test_plan_id, request.name,
        extra={"test_plan_id": new_test_plan_id},
    )
    return TestPlanCreationResponse(
        id=new_test_plan_id,
        name=request.name,
    )
