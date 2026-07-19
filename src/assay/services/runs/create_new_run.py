import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestPlanExecutionModel, TestRunModel, TestSetExecutionModel, TestStatus
from assay.schemas import (
    StandaloneRunCreationMetadata,
    TestCaseID,
    TestPlanID,
    TestPlanLiveRunCreationMetadata,
    TestSetID,
    TestSetLiveRunCreationMetadata,
    TestSetReplayedExecutionCreationMetadata,
    TestSetReplayedExecutionID,
)
from assay.services.runs._common import (
    _check_test_set_entries_have_test_types_or_409,
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404,
    _check_test_set_execution_or_404,
    _check_tests_have_test_types_or_409,
    _find_test_plan_entries_or_409,
    _find_test_set_entries_ids_or_409,
    _find_test_set_execution_id_entries_or_409,
    _find_test_sets_entries_ids_or_409,
)
from assay.services.test_plans._common import _find_test_plan_by_id_or_404
from assay.services.test_sets._common import _find_test_set_or_404
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
    
    _check_tests_have_test_types_or_409([found])
    
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


async def create_new_live_test_set_run(
        test_set_id: uuid.UUID,
        session: AsyncSession,
) -> TestSetLiveRunCreationMetadata:
    """Trigger a live execution of a test set, creating one pending run per entry.

    Runs three guards before creating anything: the test set must exist
    (404); it must have at least one entry (409) — an empty test set would
    fan out into zero runs, which would look identical to the caller as a
    successful no-op; and every entry must have at least one test type
    assigned (409) — an entry with nothing to measure it against would
    produce a run that sits pending forever with no way to ever score it.

    "Live" means the fan-out is over whatever entries the test set currently
    has, not a fixed historical scope — a later call to this same endpoint
    picks up any entries added or removed since. This is distinct from
    replaying a specific past execution, which re-targets the exact same
    entries that execution used regardless of the set's current membership.

    Creates one TestSetExecutionModel row (the trigger-event record grouping
    every run produced by this call, with replayed_execution_id left unset
    since this is a live run, not a replay) and one TestRunModel per entry,
    each pointing at that same execution — test_set_entry_id and
    test_set_execution_id set, status=pending. Enqueue-only: nothing here
    calls a model or writes back results.

    Args:
        test_set_id: UUID of the test set to execute.
        session: Active async database session.

    Returns:
        Metadata for the newly created execution: its ID, creation
        timestamp, the test set it targeted, and the number of runs created.

    Raises:
        HTTPException: 404 if no test set with the given ID exists.
        HTTPException: 409 if the test set has no entries, or if any entry
            has no test types assigned.
    """
    await _find_test_set_or_404(test_set_id, session)
    entries_ids = await _find_test_set_entries_ids_or_409(test_set_id, session)
    await _check_test_set_entries_have_test_types_or_409(entries_ids, session)

    test_set_execution_model = TestSetExecutionModel(
        id=uuid.uuid4(),
        test_set_id=test_set_id,
        created_at=datetime.now().astimezone(),
    )

    test_runs = [
        TestRunModel(
            id=uuid.uuid4(),
            status=TestStatus.pending,
            created_at=datetime.now().astimezone(),
            test_set_entry_id=test_set_entry_id,
            test_set_execution_id=test_set_execution_model.id
        )
        for test_set_entry_id in entries_ids
    ]

    session.add(test_set_execution_model)
    session.add_all(test_runs)
    await session.commit()
    
    return TestSetLiveRunCreationMetadata(
        id=test_set_execution_model.id,
        created_at=test_set_execution_model.created_at,
        test_set_id=TestSetID(id=test_set_id),
        run_count=len(entries_ids),
    )


async def create_new_replay_test_set_run(
        test_set_id: uuid.UUID,
        test_set_execution_id: uuid.UUID,
        session: AsyncSession,
) -> TestSetReplayedExecutionCreationMetadata:
    """Replay a past test set execution, creating one pending run per original entry.

    Runs three guards before creating anything: the test set must exist
    (404), the referenced execution must exist (404), and it must belong to
    this test set (404) — prevents replaying execution X of test set A
    through test set B's endpoint. The entry lookup itself (409 if the
    execution has zero runs) is handled by
    _find_test_set_execution_id_entries_or_409.

    "Replay" means the fan-out targets the exact same test_set_entry_ids the
    original execution ran, regardless of the set's current membership —
    entries removed or added to the set since have no effect. This is the
    counterpart to create_new_live_test_set_run, which always fans out over
    current membership instead.

    Creates one TestSetExecutionModel row (with replayed_execution_id set to
    the execution being replayed, marking this one as a replay rather than a
    live run) and one TestRunModel per original entry, each pointing at the
    new execution — test_set_entry_id and test_set_execution_id set,
    status=pending. Enqueue-only: nothing here calls a model or writes back
    results.

    Args:
        test_set_id: UUID of the test set the execution must belong to.
        test_set_execution_id: UUID of the past execution to replay.
        session: Active async database session.

    Returns:
        Metadata for the newly created execution: its ID, creation
        timestamp, the test set it targeted, the number of runs created,
        and the ID of the execution it replayed.

    Raises:
        HTTPException: 404 if the test set or execution doesn't exist, or
            the execution isn't linked to this test set.
        HTTPException: 409 if the execution has zero runs to replay.
    """
    await _find_test_set_or_404(test_set_id, session)
    await _check_test_set_execution_or_404(test_set_execution_id, session)
    await _check_test_set_execution_id_linked_to_specific_test_set_id_or_404(
        test_set_id, test_set_execution_id, session
    )
    found_entries = await _find_test_set_execution_id_entries_or_409(test_set_execution_id, session)

    test_set_execution_model = TestSetExecutionModel(
        id=uuid.uuid4(),
        test_set_id=test_set_id,
        replayed_execution_id=test_set_execution_id,
        created_at=datetime.now().astimezone(),
    )

    test_runs = [
        TestRunModel(
            id=uuid.uuid4(),
            status=TestStatus.pending,
            created_at=datetime.now().astimezone(),
            test_set_entry_id=test_set_entry_id,
            test_set_execution_id=test_set_execution_model.id,
        )
        for test_set_entry_id in found_entries
    ]

    session.add(test_set_execution_model)
    session.add_all(test_runs)
    await session.commit()

    return TestSetReplayedExecutionCreationMetadata(
        id=test_set_execution_model.id,
        created_at=test_set_execution_model.created_at,
        test_set_id=TestSetID(id=test_set_id),
        run_count=len(found_entries),
        replayed_execution_id=TestSetReplayedExecutionID(id=test_set_execution_id),
    )


async def create_new_live_test_plan_run(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
) -> TestPlanLiveRunCreationMetadata:
    """Trigger a live execution of a test plan, creating one pending run per
    entry across all of its linked test sets.

    Runs four guards before creating anything: the test plan must exist
    (404); it must have at least one linked test set (409) — a plan with no
    linked sets would fan out into zero runs, which would look identical to
    the caller as a successful no-op; every linked test set must have at
    least one entry (409) — same reasoning, one level down; and every entry
    across all linked sets must have at least one test type assigned (409)
    — an entry with nothing to measure it against would produce a run that
    sits pending forever with no way to ever score it.

    "Live" means the fan-out is over whichever test sets are currently
    linked to the plan and whatever entries those sets currently contain,
    not a fixed historical scope — a later call to this same endpoint picks
    up any test sets linked/unlinked or entries added/removed since. This
    is the plan-level counterpart to create_new_live_test_set_run, one
    layer up: it fans out across every linked test set's entries in a
    single execution, rather than a single set's own entries.

    Creates one TestPlanExecutionModel row (the trigger-event record
    grouping every run produced by this call, with replayed_execution_id
    left unset since this is a live run, not a replay) and one TestRunModel
    per entry across all linked test sets, each pointing at that same
    execution — test_set_entry_id and test_plan_execution_id set,
    status=pending. Enqueue-only: nothing here calls a model or writes back
    results.

    Args:
        test_plan_id: UUID of the test plan to execute.
        session: Active async database session.

    Returns:
        Metadata for the newly created execution: its ID, creation
        timestamp, the test plan it targeted, and the number of runs
        created.

    Raises:
        HTTPException: 404 if no test plan with the given ID exists.
        HTTPException: 409 if the plan has no linked test sets, if any
            linked test set has no entries, or if any entry has no test
            types assigned.
    """
    await _find_test_plan_by_id_or_404(test_plan_id, session)
    test_plan_entries_ids = await _find_test_plan_entries_or_409(test_plan_id, session)
    test_sets_entries_ids = await _find_test_sets_entries_ids_or_409(test_plan_entries_ids, session)
    await _check_test_set_entries_have_test_types_or_409(test_sets_entries_ids, session)
    
    test_plan_execution_model = TestPlanExecutionModel(
        id=uuid.uuid4(),
        test_plan_id=test_plan_id,
        created_at=datetime.now().astimezone(),
    )

    test_runs = [
        TestRunModel(
            id=uuid.uuid4(),
            status=TestStatus.pending,
            created_at=datetime.now().astimezone(),
            test_set_entry_id=test_set_entry_id,
            test_plan_execution_id=test_plan_execution_model.id,
        )
        for test_set_entry_id in test_sets_entries_ids
    ]
    
    session.add(test_plan_execution_model)
    session.add_all(test_runs)
    await session.commit()
    
    return TestPlanLiveRunCreationMetadata(
        id=test_plan_execution_model.id,
        created_at=test_plan_execution_model.created_at,
        test_plan_id=TestPlanID(id=test_plan_id),
        run_count=len(test_sets_entries_ids),
    )
