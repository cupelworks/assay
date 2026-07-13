import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestPlanEntryModel
from assay.schemas import TestPlanEntryID, TestSetID
from assay.services.test_plans._common import (
    _check_test_set_not_in_test_plan_or_409,
    _find_test_plan_by_id_or_404,
)
from assay.services.test_sets._common import _find_test_sets_or_404


async def add_test_sets_to_test_plan_by_id(
        test_plan_id: uuid.UUID,
        request: list[TestSetID],
        session: AsyncSession,
) -> list[TestPlanEntryID]:
    """Link the given test sets to a test plan, creating one entry per test set.

    Runs three guards before writing:
    1. The test plan must exist (404 otherwise).
    2. All requested test set IDs must exist (404 otherwise).
    3. None of the requested test sets may already be linked to this plan
       (409 otherwise).

    Duplicate test set IDs in the request are silently deduplicated by the SQL
    `IN` clause used in the existence check — each test set produces exactly one
    entry, regardless of how many times its ID appears in the request.

    Args:
        test_plan_id: UUID of the test plan to link test sets to.
        request: List of test set IDs to link to the plan.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        A list of TestPlanEntryID objects, one per created link.

    Raises:
        HTTPException 404: The test plan does not exist, or one or more test
            set IDs were not found.
        HTTPException 409: One or more test sets are already linked to this plan.
    """
    await _find_test_plan_by_id_or_404(test_plan_id, session)

    requested_ids = [test_set.id for test_set in request]
    test_sets_ids = await _find_test_sets_or_404(requested_ids, session)
    await _check_test_set_not_in_test_plan_or_409(
        test_plan_id, test_sets_ids, session
    )

    test_plan_entry_models = [
        TestPlanEntryModel(
            id=uuid.uuid4(),
            test_plan_id=test_plan_id,
            test_set_id=test_set_id,
        )
        for test_set_id in test_sets_ids
    ]

    session.add_all(test_plan_entry_models)
    await session.commit()

    return [
        TestPlanEntryID(
            id=test_plan_entry.id,
        )
        for test_plan_entry in test_plan_entry_models
    ]
