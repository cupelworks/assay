import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestPlanEntryModel, TestPlanExecutionModel, TestPlanModel
from assay.schemas import TestPlanName


async def _check_unique_test_plan_name_or_409(
        request: TestPlanName,
        session: AsyncSession,
) -> None:
    """Raise 409 if a test plan with the given name already exists.

    Args:
        request: Request containing the test plan name to check.
        session: Active async database session.

    Raises:
        HTTPException: 409 if the name is already taken.
    """
    found = await session.scalar(
        select(TestPlanModel.name)
        .where(TestPlanModel.name == request.name)
    )

    if found is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Test plan with name '{request.name}' already exists"
        )


async def _find_test_plan_by_id_or_404(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
):
    """Fetch a test plan by ID, raising 404 if it does not exist.

    Args:
        test_plan_id: The UUID of the test plan to look up.
        session: Active async database session.

    Returns:
        The matching TestPlanModel instance.

    Raises:
        HTTPException: 404 if no test plan with the given ID exists.
    """
    found = await session.scalar(
        select(TestPlanModel)
        .where(TestPlanModel.id == test_plan_id)
    )

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Test plan with ID '{test_plan_id}' not found"
        )

    return found


async def _check_test_set_not_in_test_plan_or_409(
        test_plan_id: uuid.UUID,
        test_sets_ids: list[uuid.UUID],
        session: AsyncSession,
) -> None:
    """Raise 409 if any of the given test sets are already linked to the test plan.

    Queries existing entries by (test_plan_id, test_set_id) and raises immediately
    if any overlap is found. The detail message lists the conflicting IDs so the
    caller knows exactly which test sets to remove from the request.

    Args:
        test_plan_id: UUID of the test plan to check against.
        test_sets_ids: List of test set UUIDs the caller intends to add.
        session: Active async database session.

    Raises:
        HTTPException: 409 listing the test set IDs already linked to the plan.
    """
    found = (await session.scalars(
        select(TestPlanEntryModel.test_set_id)
        .where(TestPlanEntryModel.test_plan_id == test_plan_id)
        .where(TestPlanEntryModel.test_set_id.in_(test_sets_ids))
    )).all()
    
    if found:
        raise HTTPException(
            status_code=409,
            detail=f"Test sets with ID '{[str(_id) for _id in found]}' "
                   f"already linked to test plan with ID '{test_plan_id}'",
        )


async def _find_test_plan_entries_or_404(
        test_plan_id: uuid.UUID,
        test_sets_ids: list[uuid.UUID],
        session: AsyncSession,
):
    """Fetch TestPlanEntryModel rows for the given test sets, scoped to this plan.

    Raises 404 if any requested test set ID does not resolve to a link in
    this plan — either because it isn't linked to any plan, or because it's
    linked to a different plan.

    Args:
        test_plan_id: UUID of the test plan the test sets must be linked to.
        test_sets_ids: IDs of the test sets to look up.
        session: Active async database session.

    Returns:
        The matching TestPlanEntryModel instances.

    Raises:
        HTTPException: 404 if one or more test set IDs aren't linked to this plan.
    """
    found = (await session.scalars(
        select(TestPlanEntryModel)
        .where(TestPlanEntryModel.test_plan_id == test_plan_id)
        .where(TestPlanEntryModel.test_set_id.in_(test_sets_ids))
    )).all()

    found_ids = {entry.test_set_id for entry in found}
    difference = set(test_sets_ids) - found_ids

    if difference:
        raise HTTPException(
            status_code=404,
            detail=f"Test sets with ID '{[str(_id) for _id in difference]}' "
                   f"not linked to test plan with ID '{test_plan_id}'"
        )

    return found


async def _check_test_plan_has_no_runs_or_409(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
) -> None:
    """Raise 409 if the test plan has ever been executed.

    Checks for the existence of a TestPlanExecutionModel rather than joining
    through to TestRunModel — an execution row always exists before any run
    attributed to it does, so this is the more direct "was this plan ever
    triggered" check.

    Args:
        test_plan_id: UUID of the test plan to check.
        session: Active async database session.

    Raises:
        HTTPException: 409 if any TestPlanExecutionModel references this plan.
    """
    found = await session.scalar(
        select(TestPlanExecutionModel.id)
        .where(TestPlanExecutionModel.test_plan_id == test_plan_id)
        .limit(1)
    )

    if found:
        raise HTTPException(
            status_code=409,
            detail=f"Test plan with ID '{test_plan_id}' has at least one run"
                   f", therefore it cannot be deleted"
        )
