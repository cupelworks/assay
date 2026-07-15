import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from assay.models import TestRunModel, TestStatus
from assay.schemas import StandaloneRunCreationMetadata, TestCaseID
from assay.services.tests._common import _find_all_tests_with_details_or_404


async def create_new_standalone_run(
        test_id: uuid.UUID,
        session: AsyncSession,
) -> StandaloneRunCreationMetadata:
    """Create a standalone, pending run for a single live test.

    Runs two guards before creating the run: the test must exist (404), and
    it must have at least one test type assigned (409) — a run against a
    test with no test types would measure nothing once executed, so it's
    rejected upfront rather than silently created as a guaranteed no-op.

    Enqueue-only: this creates a single TestRunModel with status=pending
    (test_id set, every other FK left null — the standalone mode) and
    returns immediately. Nothing here calls a model or writes back scores;
    that's separate, later work. Exactly one row is created regardless of
    how many test types are assigned.

    Args:
        test_id: UUID of the live test to create a run for.
        session: Active async database session.

    Returns:
        Metadata for the newly created run: its ID, status, creation
        timestamp, and the ID of the test it was created for.

    Raises:
        HTTPException: 404 if no test with the given ID exists.
        HTTPException: 409 if the test has no test types assigned.
    """
    found = (await _find_all_tests_with_details_or_404([test_id], session))[0]
    
    if not found.test_type_assignments:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No test types assigned to Test with id {test_id}",
        )
    
    test_run_model = TestRunModel(
        id=uuid.uuid4(),
        test_id=found.id,
        status=TestStatus.pending,
        created_at=datetime.now().astimezone(),
    )

    session.add(test_run_model)
    await session.commit()

    return StandaloneRunCreationMetadata(
        id=test_run_model.id,
        created_at=test_run_model.created_at,
        status=test_run_model.status,
        test_case_id=TestCaseID(id=found.id),
    )
