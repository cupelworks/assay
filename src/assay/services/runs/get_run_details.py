import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestRunModel
from assay.schemas import StandaloneRunDetails, TestCaseID
from assay.services.runs._common import (
    _check_test_run_by_id_or_404,
    _check_test_run_id_linked_to_specific_test_id_or_404,
)
from assay.services.tests._common import _find_test_by_id_or_404


async def get_run_details_by_test_and_run_id(
        test_id: uuid.UUID,
        test_run_id: uuid.UUID,
        session: AsyncSession,
) -> StandaloneRunDetails:
    """Orchestrates single run detail retrieval: validates the test and run IDs,
    confirms the run belongs to this test, and returns its full details
    including post-execution results.

    Three guards run before the run is fetched:
    - The test must exist (404).
    - The test run must exist (404).
    - The test run must belong to this test (404) — prevents reading run X
      of test A through test B's URL.

    Args:
        test_id: UUID of the test the run must belong to.
        test_run_id: UUID of the test run to fetch.
        session: Active async database session.

    Returns:
        The run's id, status, created_at, and test_case_id, plus scores,
        error, and executed_at — the latter three are null until the run
        reaches a terminal status (`Completed` or `Failed`).

    Raises:
        HTTPException: 404 if the test doesn't exist, the run doesn't
            exist, or the run isn't linked to this test.
    """
    await _find_test_by_id_or_404(test_id, session)
    await _check_test_run_by_id_or_404(test_run_id, session)
    await _check_test_run_id_linked_to_specific_test_id_or_404(test_id, test_run_id, session)

    found = await session.scalar(
        select(TestRunModel)
        .where(TestRunModel.id == test_run_id)
        .where(TestRunModel.test_id == test_id)
    )

    return StandaloneRunDetails(
        id=found.id,
        status=found.status,
        created_at=found.created_at,
        test_case_id=TestCaseID(id=found.test_id),
        scores=found.scores,
        error=found.error,
        executed_at=found.executed_at,
    )
