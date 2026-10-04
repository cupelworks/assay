import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import StandaloneRunModel, TestRunModel, TestSetEntryModel
from assay.schemas import (
    StandaloneRunDetails,
    TestCaseID,
    TestCaseSnapshotDate,
    TestPlanExecutionID,
    TestPlanExecutionRunDetails,
    TestPlanID,
    TestSetEntryID,
    TestSetExecutionID,
    TestSetExecutionRunDetails,
    TestSetID,
)
from assay.services.runs._common import (
    _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404,
    _check_test_plan_execution_or_404,
    _check_test_plan_or_404,
    _check_test_run_by_id_or_404,
    _check_test_run_id_linked_to_specific_test_id_or_404,
    _check_test_run_id_linked_to_specific_test_plan_execution_id_or_404,
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
        The run's id, status, created_at, batch_id and batch_index (null
        outside a statistical batch), and test_case_id, plus results,
        error, and executed_at — the latter three are null until the run
        reaches a terminal status (`Green`, `Amber`, `Red`, or `NotRan`) —
        plus the frozen copy of the test the run was created from (its
        StandaloneRunModel): name, input, expected_output, model_output,
        test_type_assignments, and test_case_snapshot_at. Read from the
        copy, not the live test, so it shows what the run was actually
        judged against even after the test has been edited.

    Raises:
        HTTPException: 404 if the test doesn't exist, the run doesn't
            exist, or the run isn't linked to this test.
    """
    await _find_test_by_id_or_404(test_id, session)
    await _check_test_run_by_id_or_404(test_run_id, session)
    await _check_test_run_id_linked_to_specific_test_id_or_404(test_id, test_run_id, session)

    test_run = (await session.execute(
        select(
            TestRunModel.status,
            TestRunModel.created_at,
            TestRunModel.batch_id,
            TestRunModel.batch_index,
            TestRunModel.results,
            TestRunModel.error,
            TestRunModel.executed_at,
            TestRunModel.evaluated_output,
            TestRunModel.output_source,
            TestRunModel.application_reply,
            StandaloneRunModel.name,
            StandaloneRunModel.input,
            StandaloneRunModel.expected_output,
            StandaloneRunModel.model_output,
            StandaloneRunModel.test_type_assignments,
            StandaloneRunModel.snapshot_at,
        )
        .join(StandaloneRunModel, TestRunModel.id == StandaloneRunModel.id)
        .where(TestRunModel.id == test_run_id)
        .where(TestRunModel.test_id == test_id)
    )).one()

    return StandaloneRunDetails(
        id=test_run_id,
        status=test_run.status,
        created_at=test_run.created_at,
        batch_id=test_run.batch_id,
        batch_index=test_run.batch_index,
        test_case_id=TestCaseID(id=test_id),
        results=test_run.results,
        error=test_run.error,
        executed_at=test_run.executed_at,
        evaluated_output=test_run.evaluated_output,
        output_source=test_run.output_source,
        application_reply=test_run.application_reply,
        name=test_run.name,
        input=test_run.input,
        expected_output=test_run.expected_output,
        model_output=test_run.model_output,
        test_type_assignments=test_run.test_type_assignments,
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=test_run.snapshot_at),
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
        The run's id, status, created_at, batch_id and batch_index (null
        outside a statistical batch), test_set_entry_id, and
        test_set_execution_id, plus results, error, and executed_at (null
        until the run reaches a terminal status), plus the snapshotted
        entry it ran against — test_case_id, name, input, expected_output,
        model_output, test_type_assignments, and test_case_snapshot_at. The
        entry is resolved via test_set_entry_id alone, not scoped to the
        entry's current test_set_id, so a run's detail stays reachable
        even after its entry has been unlinked from the set (test_set_id
        nulled) — the run and execution guards above already establish
        that this run belongs to this test set's history.

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
            TestRunModel.batch_id,
            TestRunModel.batch_index,
            TestRunModel.test_set_entry_id,
            TestRunModel.results,
            TestRunModel.error,
            TestRunModel.executed_at,
            TestRunModel.evaluated_output,
            TestRunModel.output_source,
            TestRunModel.application_reply,
            TestSetEntryModel.test_id,
            TestSetEntryModel.name,
            TestSetEntryModel.input,
            TestSetEntryModel.expected_output,
            TestSetEntryModel.model_output,
            TestSetEntryModel.test_type_assignments,
            TestSetEntryModel.snapshot_at,
        )
        .join(
            TestSetEntryModel,
            TestRunModel.test_set_entry_id == TestSetEntryModel.id,
        )
        .where(TestRunModel.id == test_run_id)
        .where(TestRunModel.test_set_execution_id == test_set_execution_id)
    )).one()

    return TestSetExecutionRunDetails(
        id=test_run_id,
        status=test_run.status,
        created_at=test_run.created_at,
        batch_id=test_run.batch_id,
        batch_index=test_run.batch_index,
        test_set_entry_id=TestSetEntryID(id=test_run.test_set_entry_id),
        test_set_execution_id=TestSetExecutionID(id=test_set_execution_id),
        results=test_run.results,
        error=test_run.error,
        executed_at=test_run.executed_at,
        evaluated_output=test_run.evaluated_output,
        output_source=test_run.output_source,
        application_reply=test_run.application_reply,
        test_case_id=TestCaseID(id=test_run.test_id),
        name=test_run.name,
        input=test_run.input,
        expected_output=test_run.expected_output,
        model_output=test_run.model_output,
        test_type_assignments=test_run.test_type_assignments,
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=test_run.snapshot_at),
        test_set_id=TestSetID(id=test_set_id),
    )


async def get_run_details_by_test_plan_execution_and_run_id(
        test_plan_id: uuid.UUID,
        test_plan_execution_id: uuid.UUID,
        test_run_id: uuid.UUID,
        session: AsyncSession,
) -> TestPlanExecutionRunDetails:
    """Orchestrates single test-plan-triggered run detail retrieval: validates
    the test plan, execution, and run IDs, confirms each is linked to the one
    before it, and returns the run's full details together with the entry it
    ran against.

    Five guards run before the run is fetched:
    - The test plan must exist (404).
    - The test plan execution must exist (404).
    - The execution must belong to this test plan (404) — prevents reading
      execution X of test plan A through test plan B's URL.
    - The test run must exist (404).
    - The test run must belong to this execution (404) — prevents reading
      run X of execution Y through execution Z's URL.

    Args:
        test_plan_id: UUID of the test plan the execution must belong to.
        test_plan_execution_id: UUID of the execution the run must belong to.
        test_run_id: UUID of the test run to fetch.
        session: Active async database session.

    Returns:
        The run's id, status, created_at, batch_id and batch_index (null
        outside a statistical batch), test_set_entry_id, and
        test_plan_execution_id, plus results, error, and executed_at (null
        until the run reaches a terminal status), plus the snapshotted
        entry it ran against — test_case_id, name, input, expected_output,
        model_output, test_type_assignments, and test_case_snapshot_at — and
        test_plan_id (the validated path parameter) and test_set_id.
        test_set_id is nullable and, unlike its counterpart in
        get_run_details_by_test_set_execution_and_run_id, isn't a
        guard-validated path parameter here — a test-plan execution can
        span multiple test sets, so there's no single "the" set to echo
        back. It's resolved live from the entry's current test_set_id
        instead, and is None once the entry has since been unlinked from
        its set. The entry itself is resolved via test_set_entry_id alone,
        not scoped to test_set_id or to the entry's test set still being
        linked to this plan (TestPlanEntryModel, which never freezes) — so
        a run's detail stays reachable regardless of either. The
        guards above already establish that this run belongs to this
        test plan's history.

    Raises:
        HTTPException: 404 if the test plan, execution, or run doesn't
            exist, or if the linkage between any of them doesn't hold.
    """
    await _check_test_plan_or_404(test_plan_id, session)
    await _check_test_plan_execution_or_404(test_plan_execution_id, session)
    await _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404(
        test_plan_id, test_plan_execution_id, session
    )
    await _check_test_run_by_id_or_404(test_run_id, session)
    await _check_test_run_id_linked_to_specific_test_plan_execution_id_or_404(
        test_plan_execution_id, test_run_id, session
    )

    test_run = (await session.execute(
        select(
            TestRunModel.status,
            TestRunModel.created_at,
            TestRunModel.batch_id,
            TestRunModel.batch_index,
            TestRunModel.test_set_entry_id,
            TestRunModel.results,
            TestRunModel.error,
            TestRunModel.executed_at,
            TestRunModel.evaluated_output,
            TestRunModel.output_source,
            TestRunModel.application_reply,
            TestSetEntryModel.test_id,
            TestSetEntryModel.test_set_id,
            TestSetEntryModel.name,
            TestSetEntryModel.input,
            TestSetEntryModel.expected_output,
            TestSetEntryModel.model_output,
            TestSetEntryModel.test_type_assignments,
            TestSetEntryModel.snapshot_at,
        )
        .join(
            TestSetEntryModel,
            TestRunModel.test_set_entry_id == TestSetEntryModel.id,
        )
        .where(TestRunModel.id == test_run_id)
        .where(TestRunModel.test_plan_execution_id == test_plan_execution_id)
    )).one()

    return TestPlanExecutionRunDetails(
        id=test_run_id,
        status=test_run.status,
        created_at=test_run.created_at,
        batch_id=test_run.batch_id,
        batch_index=test_run.batch_index,
        test_set_entry_id=TestSetEntryID(id=test_run.test_set_entry_id),
        test_plan_execution_id=TestPlanExecutionID(id=test_plan_execution_id),
        results=test_run.results,
        error=test_run.error,
        executed_at=test_run.executed_at,
        evaluated_output=test_run.evaluated_output,
        output_source=test_run.output_source,
        application_reply=test_run.application_reply,
        test_case_id=TestCaseID(id=test_run.test_id),
        name=test_run.name,
        input=test_run.input,
        expected_output=test_run.expected_output,
        model_output=test_run.model_output,
        test_type_assignments=test_run.test_type_assignments,
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=test_run.snapshot_at),
        test_set_id=TestSetID(id=test_run.test_set_id) if test_run.test_set_id else None,
        test_plan_id=TestPlanID(id=test_plan_id),
    )
