import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.services.test_plans._common import (
    _check_test_plan_has_no_runs_or_409,
    _find_test_plan_by_id_or_404,
)


async def delete_test_plan_by_id(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
) -> None:
    """Delete a test plan, along with its links to test sets.

    Runs two guards before deleting: the plan must exist (404), and it must
    have never been executed (409) — once a TestPlanExecutionModel exists for
    this plan, deleting it would destroy the audit trail's ability to say
    which campaign a past run belonged to, so it's blocked the same way
    DELETE /test-sets/{id} is blocked when an entry has runs.

    Unlike that guard, this one has nothing to do with TestPlanEntryModel
    (the plan's links to its test sets) — those never freeze regardless of
    run history, and are expected to cascade-delete
    freely alongside the plan.

    Args:
        test_plan_id: UUID of the test plan to delete.
        session: Active async database session.

    Raises:
        HTTPException: 404 if no test plan with the given ID exists.
        HTTPException: 409 if the test plan has ever been executed.
    """
    found = await _find_test_plan_by_id_or_404(test_plan_id, session)
    await _check_test_plan_has_no_runs_or_409(test_plan_id, session)

    await session.delete(found)
    await session.commit()
