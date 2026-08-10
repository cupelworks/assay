import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestRunModel, TestSetEntryModel
from assay.schemas import (
    StandaloneRunDetails,
    TestCaseID,
    TestCaseSnapshotDate,
    TestSetEntryID,
    TestSetExecutionID,
    TestSetExecutionRunDetails,
    TestSetID,
)
from assay.services.runs._common import (
    _check_test_run_by_id_or_404,
    _check_test_run_id_linked_to_specific_test_id_or_404,
    _check_test_run_id_linked_to_specific_test_set_execution_id_or_404,
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404,
    _check_test_set_execution_or_404,
)
from assay.services.test_sets._common import _find_test_set_or_404
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


async def get_run_details_by_test_set_execution_and_run_id(
        test_set_id: uuid.UUID,
        test_set_execution_id: uuid.UUID,
        test_run_id: uuid.UUID,
        session: AsyncSession,
) -> TestSetExecutionRunDetails:
    """Orchestrates single test-set-triggered run detail retrieval: validates the
    test set, execution, and run IDs, confirms each is linked to the one before
    it, and returns the run's full details together with the entry it ran against.

    Five guards run before the run is fetched:
    - The test set must exist (404).
    - The test set execution must exist (404).
    - The execution must belong to this test set (404) — prevents reading
      execution X of test set A through test set B's URL.
    - The test run must exist (404).
    - The test run must belong to this execution (404) — prevents reading
      run X of execution Y through execution Z's URL.

    Args:
        test_set_id: UUID of the test set the execution must belong to.
        test_set_execution_id: UUID of the execution the run must belong to.
        test_run_id: UUID of the test run to fetch.
        session: Active async database session.

    Returns:
        The run's id, status, created_at, test_set_entry_id, and
        test_set_execution_id, plus scores, error, and executed_at (null
        until the run reaches a terminal status), plus the snapshotted
        entry it ran against — test_case_id, name, input, expected_output,
        model_output, test_type_names, and test_case_snapshot_at.

    Raises:
        HTTPException: 404 if the test set, execution, or run doesn't
            exist, or if the linkage between any of them doesn't hold.
    """
    await _find_test_set_or_404(test_set_id, session)
    await _check_test_set_execution_or_404(test_set_execution_id, session)
    await _check_test_set_execution_id_linked_to_specific_test_set_id_or_404(
        test_set_id, test_set_execution_id, session
    )
    await _check_test_run_by_id_or_404(test_run_id, session)
    await _check_test_run_id_linked_to_specific_test_set_execution_id_or_404(
        test_set_execution_id, test_run_id, session
    )
    
    test_run = (await session.execute(
        select(
            TestRunModel.status,
            TestRunModel.created_at,
            TestRunModel.test_set_entry_id,
            TestRunModel.scores,
            TestRunModel.error,
            TestRunModel.executed_at,
            TestSetEntryModel.test_id,
            TestSetEntryModel.name,
            TestSetEntryModel.input,
            TestSetEntryModel.expected_output,
            TestSetEntryModel.model_output,
            TestSetEntryModel.test_type_names,
            TestSetEntryModel.snapshot_at,
        )
        .join(
            TestSetEntryModel,
            TestRunModel.test_set_entry_id == TestSetEntryModel.id,
        )
        .where(TestRunModel.id == test_run_id)
        .where(TestRunModel.test_set_execution_id == test_set_execution_id)
        .where(TestSetEntryModel.test_set_id == test_set_id)
    )).one()

    return TestSetExecutionRunDetails(
        id=test_run_id,
        status=test_run.status,
        created_at=test_run.created_at,
        test_set_entry_id=TestSetEntryID(id=test_run.test_set_entry_id),
        test_set_execution_id=TestSetExecutionID(id=test_set_execution_id),
        scores=test_run.scores,
        error=test_run.error,
        executed_at=test_run.executed_at,
        test_case_id=TestCaseID(id=test_run.test_id),
        name=test_run.name,
        input=test_run.input,
        expected_output=test_run.expected_output,
        model_output=test_run.model_output,
        test_type_names=test_run.test_type_names,
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=test_run.snapshot_at),
        test_set_id=TestSetID(id=test_set_id),
    )
