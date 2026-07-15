import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from assay.models import TestRunModel, TestStatus
from assay.models.test import TestSetExecutionModel
from assay.schemas import (
    StandaloneRunCreationMetadata,
    TestCaseID,
    TestSetID,
    TestSetLiveRunCreationMetadata,
)
from assay.services.runs._common import _find_test_set_entries_ids_or_409
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


async def create_new_live_test_set_run(
        test_set_id: uuid.UUID,
        session: AsyncSession,
) -> TestSetLiveRunCreationMetadata:
    """Trigger a live execution of a test set, creating one pending run per entry.

    Runs two guards before creating anything: the test set must exist (404),
    and it must have at least one entry (409) — an empty test set would fan
    out into zero runs, which would look identical to the caller as a
    successful no-op.

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
        HTTPException: 409 if the test set has no entries.
    """
    await _find_test_set_or_404(test_set_id, session)
    entries_ids = await _find_test_set_entries_ids_or_409(test_set_id, session)

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
    