# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import (
    StandaloneRunModel,
    TestPlanExecutionModel,
    TestRunModel,
    TestSetExecutionModel,
    TestStatus,
)
from assay.schemas import (
    StandaloneRunCreationMetadata,
    TestCaseID,
    TestPlanID,
    TestPlanLiveRunCreationMetadata,
    TestPlanReplayedExecutionCreationMetadata,
    TestPlanReplayedExecutionID,
    TestSetID,
    TestSetLiveRunCreationMetadata,
    TestSetReplayedExecutionCreationMetadata,
    TestSetReplayedExecutionID,
)
from assay.services.runs._common import (
    _add_runs,
    _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404,
    _check_test_plan_execution_or_404,
    _check_test_set_entries_have_test_types_or_409,
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404,
    _check_test_set_execution_or_404,
    _check_tests_have_test_types_or_409,
    _dispatch_runs,
    _find_test_plan_entries_or_409,
    _find_test_plan_execution_id_entries_or_409,
    _find_test_set_entries_ids_or_409,
    _find_test_set_execution_id_entries_or_409,
    _find_test_sets_entries_ids_or_409,
)
from assay.services.test_plans._common import _find_test_plan_by_id_or_404
from assay.services.test_sets._common import _find_test_set_or_404
from assay.services.tests._common import (
    _find_all_tests_with_details_or_404,
    _frozen_test_type_assignments,
)
from assay.timestamps import utc_now

logger = logging.getLogger(__name__)


def _new_standalone_run(test, batch_id: uuid.UUID | None = None,
                        batch_index: int | None = None) -> TestRunModel:
    """A pending standalone run of a live test, with its frozen copy. Not
    added to a session. A statistical batch passes its id and the time."""
    created_at = utc_now()
    run = TestRunModel(
        id=uuid.uuid4(),
        test_id=test.id,
        status=TestStatus.pending,
        created_at=created_at,
        batch_id=batch_id,
        batch_index=batch_index,
    )
    run.standalone_run = StandaloneRunModel(
        id=run.id,
        name=test.name,
        input=test.input,
        expected_output=test.expected_output,
        model_output=test.model_output,
        test_type_assignments=_frozen_test_type_assignments(test),
        snapshot_at=created_at,
    )
    return run


def _new_test_set_execution(
        test_set_id: uuid.UUID,
        entry_ids: list[uuid.UUID],
        replayed_execution_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        batch_index: int | None = None,
) -> tuple[TestSetExecutionModel, list[TestRunModel]]:
    """A test set execution and one pending run per entry. Not added to a
    session."""
    execution = TestSetExecutionModel(
        id=uuid.uuid4(),
        test_set_id=test_set_id,
        replayed_execution_id=replayed_execution_id,
        created_at=utc_now(),
        batch_id=batch_id,
        batch_index=batch_index,
    )
    runs = [
        TestRunModel(
            id=uuid.uuid4(),
            status=TestStatus.pending,
            created_at=utc_now(),
            test_set_entry_id=entry_id,
            test_set_execution_id=execution.id,
            batch_id=batch_id,
            batch_index=batch_index,
        )
        for entry_id in entry_ids
    ]
    return execution, runs


def _new_test_plan_execution(
        test_plan_id: uuid.UUID,
        entry_ids: list[uuid.UUID],
        replayed_execution_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        batch_index: int | None = None,
) -> tuple[TestPlanExecutionModel, list[TestRunModel]]:
    """A test plan execution and one pending run per entry. Not added to a
    session."""
    execution = TestPlanExecutionModel(
        id=uuid.uuid4(),
        test_plan_id=test_plan_id,
        replayed_execution_id=replayed_execution_id,
        created_at=utc_now(),
        batch_id=batch_id,
        batch_index=batch_index,
    )
    runs = [
        TestRunModel(
            id=uuid.uuid4(),
            status=TestStatus.pending,
            created_at=utc_now(),
            test_set_entry_id=entry_id,
            test_plan_execution_id=execution.id,
            batch_id=batch_id,
            batch_index=batch_index,
        )
        for entry_id in entry_ids
    ]
    return execution, runs


async def create_new_standalone_run(
        test_id: uuid.UUID,
        session: AsyncSession,
) -> StandaloneRunCreationMetadata:
    """Create a standalone, pending run for a single live test.

    Runs two guards before creating the run: the test must exist (404), and
    it must have at least one test type assigned (409) — a run against a
    test with no test types would measure nothing once executed, so it's
    rejected upfront rather than silently created as a guaranteed no-op.

    Creates a single TestRunModel with status=pending (test_id set, every
    other FK left null — the standalone mode) together with its
    StandaloneRunModel — the frozen copy of the test (name, input, outputs,
    assigned types with their config) this run is evaluated against, so
    later edits to the live test never change what the run was judged by.
    Both are committed in one transaction, then the run is dispatched for
    execution (best-effort, see _dispatch_runs) before returning. Nothing
    here calls a model or writes back scores itself — that happens in the
    worker process once it picks up the dispatched task. Exactly one run is
    created regardless of how many test types are assigned.

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
    
    test_run_model = _new_standalone_run(found)

    await _add_runs(session, [test_run_model])
    await session.commit()

    logger.info(
        "Created standalone run %s for test %s", test_run_model.id, found.id,
        extra={"run_id": test_run_model.id, "test_id": found.id},
    )
    _dispatch_runs([test_run_model.id])

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
    test_set_execution_id set, status=pending. Every created run is then
    dispatched for execution (best-effort, see _dispatch_runs); nothing
    here calls a model or writes back results itself.

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

    test_set_execution_model, test_runs = _new_test_set_execution(test_set_id, entries_ids)

    await _add_runs(session, test_runs, [test_set_execution_model])
    await session.commit()

    logger.info(
        "Created live execution %s of test set %s with %d runs",
        test_set_execution_model.id, test_set_id, len(test_runs),
        extra={
            "test_set_execution_id": test_set_execution_model.id,
            "test_set_id": test_set_id,
            "run_count": len(test_runs),
        },
    )
    _dispatch_runs([test_run.id for test_run in test_runs])

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
    status=pending. Every created run is then dispatched for execution
    (best-effort, see _dispatch_runs); nothing here calls a model or writes
    back results itself.

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

    test_set_execution_model, test_runs = _new_test_set_execution(
        test_set_id, found_entries, replayed_execution_id=test_set_execution_id)

    await _add_runs(session, test_runs, [test_set_execution_model])
    await session.commit()

    logger.info(
        "Created execution %s of test set %s replaying execution %s with %d runs",
        test_set_execution_model.id, test_set_id, test_set_execution_id, len(test_runs),
        extra={
            "test_set_execution_id": test_set_execution_model.id,
            "test_set_id": test_set_id,
            "replayed_execution_id": test_set_execution_id,
            "run_count": len(test_runs),
        },
    )
    _dispatch_runs([test_run.id for test_run in test_runs])

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
    status=pending. Every created run is then dispatched for execution
    (best-effort, see _dispatch_runs); nothing here calls a model or writes
    back results itself.

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
    
    test_plan_execution_model, test_runs = _new_test_plan_execution(
        test_plan_id, test_sets_entries_ids)

    await _add_runs(session, test_runs, [test_plan_execution_model])
    await session.commit()

    logger.info(
        "Created live execution %s of test plan %s with %d runs",
        test_plan_execution_model.id, test_plan_id, len(test_runs),
        extra={
            "test_plan_execution_id": test_plan_execution_model.id,
            "test_plan_id": test_plan_id,
            "run_count": len(test_runs),
        },
    )
    _dispatch_runs([test_run.id for test_run in test_runs])

    return TestPlanLiveRunCreationMetadata(
        id=test_plan_execution_model.id,
        created_at=test_plan_execution_model.created_at,
        test_plan_id=TestPlanID(id=test_plan_id),
        run_count=len(test_sets_entries_ids),
    )


async def create_new_replay_test_plan_run(
        test_plan_id: uuid.UUID,
        test_plan_execution_id: uuid.UUID,
        session: AsyncSession,
) -> TestPlanReplayedExecutionCreationMetadata:
    """Replay a past test plan execution, creating one pending run per original entry.

    Runs three guards before creating anything: the test plan must exist
    (404), the referenced execution must exist (404), and it must belong to
    this test plan (404) — prevents replaying execution X of test plan A
    through test plan B's endpoint. The entry lookup itself (409 if the
    execution has zero runs) is handled by
    _find_test_plan_execution_id_entries_or_409. Deliberately no test-type
    guard here (unlike create_new_live_test_plan_run) — every entry reached
    this way already passed that check when its run was first created via
    the live path, and stays frozen for as long as that run exists.

    "Replay" means the fan-out targets the exact same test_set_entry_ids the
    original execution ran, regardless of the plan's current linked test
    sets — test sets linked or unlinked since have no effect. This is the
    counterpart to create_new_live_test_plan_run, which always fans out over
    the plan's current linked test sets instead.

    Creates one TestPlanExecutionModel row (with replayed_execution_id set
    to the execution being replayed, marking this one as a replay rather
    than a live run) and one TestRunModel per original entry, each pointing
    at the new execution — test_set_entry_id and test_plan_execution_id
    set, status=pending. Every created run is then dispatched for execution
    (best-effort, see _dispatch_runs); nothing here calls a model or writes
    back results itself.

    Args:
        test_plan_id: UUID of the test plan the execution must belong to.
        test_plan_execution_id: UUID of the past execution to replay.
        session: Active async database session.

    Returns:
        Metadata for the newly created execution: its ID, creation
        timestamp, the test plan it targeted, the number of runs created,
        and the ID of the execution it replayed.

    Raises:
        HTTPException: 404 if the test plan or execution doesn't exist, or
            the execution isn't linked to this test plan.
        HTTPException: 409 if the execution has zero runs to replay.
    """
    await _find_test_plan_by_id_or_404(test_plan_id, session)
    await _check_test_plan_execution_or_404(test_plan_execution_id, session)
    await _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404(
        test_plan_id, test_plan_execution_id, session
    )

    found_entries = await _find_test_plan_execution_id_entries_or_409(
        test_plan_execution_id, session
    )

    test_plan_execution_model, test_runs = _new_test_plan_execution(
        test_plan_id, found_entries, replayed_execution_id=test_plan_execution_id)

    await _add_runs(session, test_runs, [test_plan_execution_model])
    await session.commit()

    logger.info(
        "Created execution %s of test plan %s replaying execution %s with %d runs",
        test_plan_execution_model.id, test_plan_id, test_plan_execution_id, len(test_runs),
        extra={
            "test_plan_execution_id": test_plan_execution_model.id,
            "test_plan_id": test_plan_id,
            "replayed_execution_id": test_plan_execution_id,
            "run_count": len(test_runs),
        },
    )
    _dispatch_runs([test_run.id for test_run in test_runs])

    return TestPlanReplayedExecutionCreationMetadata(
        id=test_plan_execution_model.id,
        created_at=test_plan_execution_model.created_at,
        test_plan_id=TestPlanID(id=test_plan_id),
        run_count=len(found_entries),
        replayed_execution_id=TestPlanReplayedExecutionID(id=test_plan_execution_id),
    )
