import logging
import uuid
from collections.abc import Iterable

from fastapi import HTTPException
from kombu.exceptions import KombuError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.logging_config import request_id_var
from assay.models import (
    TestModel,
    TestPlanEntryModel,
    TestPlanExecutionModel,
    TestPlanModel,
    TestRunModel,
    TestSetEntryModel,
    TestSetExecutionModel,
)
from assay.run_check_types import with_check_types
from assay.worker import app as _celery_app

logger = logging.getLogger(__name__)

# The worker's task that executes one run, published by name (_dispatch_runs)
EXECUTE_RUN_TASK = "assay.worker.tasks.execute_run.execute_run"


async def _find_test_set_entries_ids_or_409(
        test_set_id: uuid.UUID,
        session: AsyncSession,
) -> list[uuid.UUID]:
    """Fetch test set's entry IDs, raising 409 if it has none.

    A test set existing is not enough to make it a valid live-execution
    target — an empty test set would fan out into zero runs, which looks
    identical to the caller as a successful no-op. Rejecting it upfront
    surfaces the mistake immediately, the same reasoning already applied to
    empty test plan scope and to standalone runs against a test with no
    test types assigned. Does not check whether the test set itself exists
    — callers are expected to run that check first.

    Returns the entry IDs directly (not just a count) so the caller can
    build one TestRunModel per entry without a second query.

    Args:
        test_set_id: UUID of the test set to fetch entry IDs for.
        session: Active async database session.

    Returns:
        The IDs of every entry currently in the test set (always >= 1 item).

    Raises:
        HTTPException: 409 if the test set has zero entries.
    """
    entries_ids = list((await session.scalars(
        select(TestSetEntryModel.id)
        .where(TestSetEntryModel.test_set_id == test_set_id)
    )).all())
    
    if not entries_ids:
        raise HTTPException(
            status_code=409,
            detail=f"No Test Set Entries found in Test set with ID '{test_set_id}'"
        )

    return entries_ids


async def _find_test_sets_entries_ids_or_409(
        test_sets_ids: list[uuid.UUID],
        session: AsyncSession,
) -> list[uuid.UUID]:
    """Fetch entry IDs across multiple test sets, raising 409 if any has none.

    Batches the check across every given test set in a single query rather
    than one query per set, and reports every empty test set together
    instead of failing fast on the first one found. A test-plan
    live execution fans out over every entry in every linked test set;
    if even one linked set were empty, that
    set alone would silently contribute zero runs, undermining the
    reproducibility of the campaign's results as a whole. Does not check whether
    the given test set IDs themselves exist — callers are expected to run that
    check first.

    Returns the entry IDs directly (not just a count) so the caller can
    build one TestRunModel per entry without a second query.

    Args:
        test_sets_ids: UUIDs of the test sets to fetch entry IDs for.
        session: Active async database session.

    Returns:
        The IDs of every entry across all given test sets (every set has
        at least one entry, or this raises instead of returning).

    Raises:
        HTTPException: 409 if any of the given test sets has zero entries.
    """
    found = (await session.execute(
        select(TestSetEntryModel.id, TestSetEntryModel.test_set_id)
        .where(TestSetEntryModel.test_set_id.in_(test_sets_ids))
    )).all()

    found_test_sets_ids = set(row.test_set_id for row in found)
    not_found = [
        test_set_id for test_set_id in test_sets_ids
        if test_set_id not in found_test_sets_ids
    ]

    if len(not_found) > 0:
        raise HTTPException(
            status_code=409,
            detail=f"No Test Set Entries found in Test sets with IDs "
                   f"{[str(test_set_id) for test_set_id in not_found]}"
        )

    return [row.id for row in found]


def _check_tests_have_test_types_or_409(
        tests: list[TestModel],
) -> None:
    """Raise 409 if any given test has zero test type assignments.

    A run against a test with nothing to measure it against would be
    created only to sit pending forever with no way to ever produce a
    score. Every offending test is reported together in one 409 rather
    than failing on the first one found. Callers are expected to have
    already loaded each TestModel's test_type_assignments (e.g. via
    _find_all_tests_with_details_or_404) — this function does no
    querying of its own.

    Args:
        tests: TestModel instances to check, each with test_type_assignments
            already loaded.

    Raises:
        HTTPException: 409 if any given test has zero test type assignments.
    """
    no_test_type_assignments = []

    for test in tests:
        if not test.test_type_assignments:
            no_test_type_assignments.append(test)

    if len(no_test_type_assignments) > 0:
        raise HTTPException(
            status_code=409,
            detail=f"No test types assigned to Tests with ids "
                   f"{[str(test.id) for test in no_test_type_assignments]}"
        )


async def _check_test_set_entries_have_test_types_or_409(
        test_set_entry_ids: list[uuid.UUID],
        session: AsyncSession,
) -> None:
    """Raise 409 if any given test set entry has zero test type assignments.

    A run against an entry with nothing to measure it against would be
    created only to sit pending forever with no way to ever produce a
    score. Every offending entry is reported together in one 409 rather
    than failing on the first one found.

    test_type_assignments is a plain JSON column snapshotted onto the entry
    at inclusion time (see TestSetEntryModel), not a relationship — so
    unlike the TestModel equivalent, this needs no selectinload or join,
    just the two columns actually used. Callers pass the specific entry IDs
    to check; this does not check whether the entries' test sets themselves
    exist or are non-empty — callers are expected to have run that check first.

    Args:
        test_set_entry_ids: UUIDs of the test set entries to check.
        session: Active async database session.

    Raises:
        HTTPException: 409 if any given entry has zero test type assignments.
    """
    found = (await session.execute(
        select(TestSetEntryModel.id, TestSetEntryModel.test_type_assignments)
        .where(TestSetEntryModel.id.in_(test_set_entry_ids))
    )).all()

    no_test_type_assignments = [row.id for row in found if not row.test_type_assignments]

    if len(no_test_type_assignments) > 0:
        raise HTTPException(
            status_code=409,
            detail=f"No test types assigned to Test Set Entries with ids "
                   f"{[str(entry_id) for entry_id in no_test_type_assignments]}"
        )


async def _check_test_set_execution_or_404(
        test_set_execution_id: uuid.UUID,
        session: AsyncSession,
):
    """Raise 404 if no test set execution with the given ID exists.

    Unscoped — does not check which test set the execution belongs to. Use
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404 when
    the execution also needs to be scoped to a specific test set, so the two
    failure modes ("doesn't exist at all" vs. "exists, but not for this
    test set") get distinct, precise messages instead of being collapsed
    into one.

    Args:
        test_set_execution_id: UUID of the test set execution to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if no test set execution with this ID exists.
    """
    found = await session.scalar(
        select(TestSetExecutionModel.id)
        .where(TestSetExecutionModel.id == test_set_execution_id)
    )

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Test set execution with ID '{test_set_execution_id}' does not exist"
        )
    
    
async def _check_test_plan_execution_or_404(
        test_plan_execution_id: uuid.UUID,
        session: AsyncSession,
):
    """Raise 404 if no test plan execution with the given ID exists.

    Unscoped — does not check which test plan the execution belongs to,
    mirroring _check_test_set_execution_or_404 one layer up: keeping
    "doesn't exist at all" as its own precise message, separate from
    "exists, but not for this plan", is what lets a scoped variant built on
    top of this one give each failure mode its own message instead of
    collapsing them into one.

    Args:
        test_plan_execution_id: UUID of the test plan execution to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if no test plan execution with this ID exists.
    """
    found = await session.scalar(
        select(TestPlanExecutionModel.id)
        .where(TestPlanExecutionModel.id == test_plan_execution_id)
    )

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Test plan execution with ID '{test_plan_execution_id}' does not exist"
        )


async def _check_test_set_execution_id_linked_to_specific_test_set_id_or_404(
        test_set_id: uuid.UUID,
        test_set_execution_id: uuid.UUID,
        session: AsyncSession,
):
    """Raise 404 if the given execution doesn't belong to the given test set.

    Scoped lookup, same shape as _find_test_plan_entries_or_404: prevents a
    caller from replaying execution X of test set A by hitting test set B's
    endpoint. Callers are expected to have already confirmed the execution
    exists at all (via _check_test_set_execution_or_404) — this only
    distinguishes "exists, but belongs to a different test set" from that.

    Args:
        test_set_id: UUID of the test set the execution must belong to.
        test_set_execution_id: UUID of the test set execution to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if the execution isn't linked to this test set.
    """
    found = await session.scalar(
        select(TestSetExecutionModel.id)
        .where(TestSetExecutionModel.id == test_set_execution_id)
        .where(TestSetExecutionModel.test_set_id == test_set_id)
    )

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Test set execution with ID '{test_set_execution_id}' not linked "
                   f"to test set with ID '{test_set_id}'"
        )


async def _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404(
        test_plan_id: uuid.UUID,
        test_plan_execution_id: uuid.UUID,
        session: AsyncSession,
):
    """Raise 404 if the given execution doesn't belong to the given test plan.

    Scoped lookup, mirroring _check_test_set_execution_id_linked_to_specific_test_set_id_or_404
    one layer up: prevents a caller from replaying execution X of test plan A
    by hitting test plan B's endpoint. Callers are expected to have already
    confirmed the execution exists at all (via
    _check_test_plan_execution_or_404) — this only distinguishes "exists,
    but belongs to a different test plan" from that.

    Args:
        test_plan_id: UUID of the test plan the execution must belong to.
        test_plan_execution_id: UUID of the test plan execution to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if the execution isn't linked to this test plan.
    """
    found = await session.scalar(
        select(TestPlanExecutionModel.id)
        .where(TestPlanExecutionModel.id == test_plan_execution_id)
        .where(TestPlanExecutionModel.test_plan_id == test_plan_id)
    )

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Test plan execution with ID '{test_plan_execution_id}' not linked "
                   f"to test plan with ID '{test_plan_id}'"
        )


async def _find_test_set_execution_id_entries_or_409(
        test_set_execution_id: uuid.UUID,
        session: AsyncSession,
):
    """Fetch a test set execution's run entry IDs, raising 409 if it has none.

    An entry can be unlinked from its set (test_set_id set to NULL) after
    being run without invalidating the run itself, so reading through
    test_set_execution_id here keeps returning every entry that execution
    actually ran, regardless of current set membership. Callers needing
    "does this execution belong to this test set" should use
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404 first
    — this function only reads the execution's already-frozen run history.

    Same empty-target reasoning as _find_test_set_entries_ids_or_409: a
    replay execution with zero runs to reproduce would silently succeed
    with an empty scope, indistinguishable to the caller from a real
    replay.

    Args:
        test_set_execution_id: UUID of the test set execution to fetch entry IDs for.
        session: Active async database session.

    Returns:
        The test_set_entry_id of every run belonging to this execution (always >= 1 item).

    Raises:
        HTTPException: 409 if the execution has zero runs.
    """
    found = list((await session.scalars(
        select(TestRunModel.test_set_entry_id)
        .where(TestRunModel.test_set_execution_id == test_set_execution_id)
    )).all())

    if not found:
        raise HTTPException(
            status_code=409,
            detail=f"Test set execution with ID '{test_set_execution_id}' has no test set entries"
        )

    return found


async def _find_test_plan_execution_id_entries_or_409(
        test_plan_execution_id: uuid.UUID,
        session: AsyncSession,
):
    """Fetch a test plan execution's run entry IDs, raising 409 if it has none.

    A test set can be unlinked from the plan (its TestPlanEntryModel link
    removed) after being run without invalidating the run itself, so
    reading through test_plan_execution_id here keeps returning every
    entry that execution actually ran, regardless of the plan's current
    linked test sets. Callers needing "does this execution belong to this
    test plan" should use
    _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404
    first — this function only reads the execution's already-frozen run
    history.

    Same empty-target reasoning as _find_test_set_execution_id_entries_or_409:
    a replay execution with zero runs to reproduce would silently succeed
    with an empty scope, indistinguishable to the caller from a real
    replay.

    Args:
        test_plan_execution_id: UUID of the test plan execution to fetch entry IDs for.
        session: Active async database session.

    Returns:
        The test_set_entry_id of every run belonging to this execution (always >= 1 item).

    Raises:
        HTTPException: 409 if the execution has zero runs.
    """
    found = list((await session.scalars(
        select(TestRunModel.test_set_entry_id)
        .where(TestRunModel.test_plan_execution_id == test_plan_execution_id)
    )).all())

    if not found:
        raise HTTPException(
            status_code=409,
            detail=f"Test plan execution with ID '{test_plan_execution_id}' has no test set entries"
        )

    return found


async def _find_test_plan_entries_or_409(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
) -> list[uuid.UUID]:
    """Fetch the test plan's linked test_set_ids, raising 409 if it has none.

    A test plan with no linked test sets would fan out into zero runs for a
    live execution — indistinguishable from a successful no-op — so it's
    rejected upfront, the same reasoning already applied to empty test sets
    (_find_test_set_entries_ids_or_409) and to standalone runs against a
    test with no test types assigned. Does not check whether the test plan
    itself exists — callers are expected to run that check first.

    Returns the test_set_ids directly (not just a count) so the caller can
    join to TestSetEntryModel across all of them — for every entry in every
    linked set — without a second lookup of which sets are actually linked.

    Args:
        test_plan_id: UUID of the test plan to fetch linked test set IDs for.
        session: Active async database session.

    Returns:
        The test_set_id of every test set linked to the plan (always >= 1 item).

    Raises:
        HTTPException: 409 if the test plan has zero linked test sets.
    """
    test_plan_entries = list((await session.scalars(
        select(TestPlanEntryModel.test_set_id)
        .where(TestPlanEntryModel.test_plan_id == test_plan_id)
    )).all())

    if len(test_plan_entries) == 0:
        raise HTTPException(
            status_code=409,
            detail=f"Test plan with ID '{test_plan_id}' has no linked test sets"
        )

    return test_plan_entries


async def _check_test_run_by_id_or_404(
        test_run_id: uuid.UUID,
        session: AsyncSession,
) -> None:
    """Raise 404 if no test run with the given ID exists.

    Unscoped — does not check which test the run belongs to. Use
    _check_test_run_id_linked_to_specific_test_id_or_404 when the run also
    needs to be scoped to a specific test, so the two failure modes
    ("doesn't exist at all" vs. "exists, but not for this test") get
    distinct, precise messages instead of being collapsed into one.

    Args:
        test_run_id: UUID of the test run to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if no test run with this ID exists.
    """
    found = await session.scalar(
        select(TestRunModel.id)
        .where(TestRunModel.id == test_run_id)
    )

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Test run with ID '{test_run_id}' does not exist"
        )


async def _check_test_run_id_linked_to_specific_test_id_or_404(
        test_id: uuid.UUID,
        test_run_id: uuid.UUID,
        session: AsyncSession,
) -> None:
    """Raise 404 if the given run doesn't belong to the given test.

    Scoped lookup, mirroring
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404 one
    layer down: prevents a caller from reading run X of test A by hitting
    test B's endpoint. Callers are expected to have already confirmed the
    run exists at all (via _check_test_run_by_id_or_404) — this only
    distinguishes "exists, but belongs to a different test" from that.

    Args:
        test_id: UUID of the test the run must belong to.
        test_run_id: UUID of the test run to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if the run isn't linked to this test.
    """
    found = await session.scalar(
        select(TestRunModel.id)
        .where(TestRunModel.id == test_run_id)
        .where(TestRunModel.test_id == test_id)
    )

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Test run with ID '{test_run_id}' not linked "
                   f"to test with ID '{test_id}'"
        )


async def _check_test_run_id_linked_to_specific_test_set_execution_id_or_404(
        test_set_execution_id: uuid.UUID,
        test_run_id: uuid.UUID,
        session: AsyncSession,
) -> None:
    """Raise 404 if the given run doesn't belong to the given test set execution.

    Scoped lookup, same shape as
    _check_test_run_id_linked_to_specific_test_id_or_404 one layer up:
    prevents a caller from reading run X of execution Y by hitting execution
    Z's endpoint. Callers are expected to have already confirmed the run
    exists at all (via _check_test_run_by_id_or_404) — this only
    distinguishes "exists, but belongs to a different execution" from that.

    Args:
        test_set_execution_id: UUID of the test set execution the run must belong to.
        test_run_id: UUID of the test run to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if the run isn't linked to this test set execution.
    """
    found = await session.scalar(
        select(TestRunModel.id)
        .where(TestRunModel.id == test_run_id)
        .where(TestRunModel.test_set_execution_id == test_set_execution_id)
    )

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Test run with ID '{test_run_id}' not linked "
                   f"to test set execution with ID '{test_set_execution_id}'"
        )


async def _check_test_run_id_linked_to_specific_test_plan_execution_id_or_404(
        test_plan_execution_id: uuid.UUID,
        test_run_id: uuid.UUID,
        session: AsyncSession,
) -> None:
    """Raise 404 if the given run doesn't belong to the given test plan execution.

    Scoped lookup, mirroring
    _check_test_run_id_linked_to_specific_test_set_execution_id_or_404 one
    layer up: prevents a caller from reading run X of execution Y by hitting
    execution Z's endpoint. Callers are expected to have already confirmed
    the run exists at all (via _check_test_run_by_id_or_404) — this only
    distinguishes "exists, but belongs to a different execution" from that.

    Args:
        test_plan_execution_id: UUID of the test plan execution the run
            must belong to.
        test_run_id: UUID of the test run to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if the run isn't linked to this test plan execution.
    """
    found = await session.scalar(
        select(TestRunModel.id)
        .where(TestRunModel.id == test_run_id)
        .where(TestRunModel.test_plan_execution_id == test_plan_execution_id)
    )

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Test run with ID '{test_run_id}' not linked "
                   f"to test plan execution with ID '{test_plan_execution_id}'"
        )


async def _check_test_plan_or_404(test_plan_id: uuid.UUID, session: AsyncSession) -> None:
    """Raise 404 if no test plan with the given ID exists.

    Deliberately selects only TestPlanModel.id rather than the full row —
    unlike _find_test_plan_by_id_or_404 (test_plans/_common.py), which
    fetches and hydrates the whole model. Callers here only need existence
    confirmed, never the model instance itself, so there's no reason to
    pay for the extra columns or ORM construction.

    Unscoped — does not check anything about the plan beyond existence.

    Args:
        test_plan_id: UUID of the test plan to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if no test plan with this ID exists.
    """
    found = await session.scalar(
        select(TestPlanModel.id)
        .where(TestPlanModel.id == test_plan_id)
    )

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Test plan with ID '{test_plan_id}' not found"
        )


async def _add_runs(session: AsyncSession, runs: list[TestRunModel],
                   executions: Iterable = ()) -> None:
    """Add new runs, and the executions they belong to, to the session, each
    run with its check types. Call it once the runs' skip labels are set."""
    entry_ids = {run.test_set_entry_id for run in runs if run.test_set_entry_id is not None}
    assignments = dict((await session.execute(
        select(TestSetEntryModel.id, TestSetEntryModel.test_type_assignments)
        .where(TestSetEntryModel.id.in_(entry_ids)))).tuples().all()) if entry_ids else {}
    with_check_types(runs, assignments)
    session.add_all([*executions, *runs])


def _dispatch_runs(run_ids: list[uuid.UUID]) -> None:
    """Publish an execute_run task for each given run id, best-effort.

    Callers must only invoke this after the row(s) have already been
    committed to the database — the worker picks up a run through its own,
    separate database connection, which won't see an uncommitted row yet.

    Publishes by task name (send_task) rather than calling execute_run.delay()
    directly. .delay() requires importing the actual execute_run function,
    which pulls in its whole dependency chain — the evaluator modules, and
    eventually the anthropic/openai SDKs once the LLM-as-judge evaluator is
    real — into this process just to send a message. send_task only needs a
    Celery app configured with a broker URL, so the API can dispatch work
    without importing anything the worker itself depends on.

    Publishing is fire-and-forget: each run's send is wrapped in its own
    try/except KombuError, so one run failing to publish (a broker outage, a
    network blip, an encoding failure) never stops the rest of a batch from
    being attempted. A run whose dispatch fails simply stays Pending, to be
    re-published by the reconciliation scan. Anything that isn't a KombuError
    is a bug, not a publish failure, and propagates.

    ignore_result=True is passed explicitly: send_task doesn't read the app's
    task_ignore_result setting, and without it send_task subscribes to the
    result store before publishing — which fails with a generic RuntimeError
    (and, per Celery, leaves the process needing a restart) whenever the
    result store is unreachable, even though no result is ever read back.

    Args:
        run_ids: UUIDs of the already-committed TestRunModel rows to
            dispatch execute_run for.
    """
    dispatched = 0
    for run_id in run_ids:
        try:
            _celery_app.send_task(
                EXECUTE_RUN_TASK,
                args=[run_id],
                ignore_result=True,
                # travels in the task message, so the worker's log lines for
                # this run carry the same request ID as this request's
                headers={"request_id": request_id_var.get()},
            )
        except KombuError:
            logger.exception("Failed to dispatch execute_run for run %s", run_id)
        else:
            dispatched += 1

    if run_ids:
        logger.info(
            "Dispatched %d of %d runs to the worker", dispatched, len(run_ids),
            extra={"dispatched": dispatched, "run_count": len(run_ids)},
        )


def _batch_clause(column, batch: str | None):
    """The `?batch=` filter of a run or execution listing as a WHERE clause:
    `none` keeps what no statistical batch created, a batch's id keeps that
    batch's, None (left out) keeps everything — returned as None."""
    if batch is None:
        return None
    if batch == "none":
        return column.is_(None)
    return column == uuid.UUID(batch)
