import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestPlanEntryModel
from assay.schemas import TestSetID
from assay.services.test_plans._common import (
    _find_test_plan_by_id_or_404,
    _find_test_plan_entries_or_404,
)
from assay.services.test_sets._common import _find_test_sets_or_404


async def remove_test_sets_from_test_plan_by_id(
        test_plan_id: uuid.UUID,
        request: list[TestSetID],
        session: AsyncSession,
) -> None:
    """Unlink the given test sets from a test plan, deleting their entries.

    Runs three guards before writing:
    1. The test plan must exist (404 otherwise).
    2. All requested test set IDs must exist (404 otherwise).
    3. All requested test sets must currently be linked to this plan (404
       otherwise).

    Unlinking is unconditional — a test set already referenced by
    TestRunModel rows via this plan can still be removed. Nothing about
    those runs depends on the TestPlanEntryModel link continuing to exist:
    each run's reproducibility comes from its frozen TestSetEntryModel, not
    from this junction row (see TestPlanEntryModel's docstring).

    Duplicate test set IDs in the request are harmless — the `IN` clauses
    used by each guard and the final delete collapse them naturally.

    Args:
        test_plan_id: UUID of the test plan to unlink test sets from.
        request: List of test set IDs to unlink from the plan.
        session: Async SQLAlchemy session injected by FastAPI.

    Raises:
        HTTPException 404: The test plan does not exist, one or more test
            set IDs were not found, or one or more test sets are not
            currently linked to this plan.
    """
    await _find_test_plan_by_id_or_404(test_plan_id, session)

    test_sets_ids = [_id.id for _id in request]
    await _find_test_sets_or_404(test_sets_ids, session)

    await _find_test_plan_entries_or_404(
        test_plan_id, test_sets_ids, session
    )

    await session.execute(
        delete(TestPlanEntryModel)
        .where(TestPlanEntryModel.test_plan_id == test_plan_id)
        .where(TestPlanEntryModel.test_set_id.in_(test_sets_ids))
    )
    await session.commit()
