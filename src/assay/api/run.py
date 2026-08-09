import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import (
    PaginatedStandaloneRunCreationMetadata,
    PaginatedTestSetRunCreationMetadata,
    StandaloneRunCreationMetadata,
    StandaloneRunDetails,
    TestPlanLiveRunCreationMetadata,
    TestPlanReplayedExecutionCreationMetadata,
    TestSetLiveRunCreationMetadata,
    TestSetReplayedExecutionCreationMetadata,
)
from assay.services import (
    create_new_live_test_plan_run,
    create_new_live_test_set_run,
    create_new_replay_test_plan_run,
    create_new_replay_test_set_run,
    create_new_standalone_run,
    get_run_details_by_test_and_run_id,
    get_standalone_run_metadata_all_test_runs,
    get_test_set_run_metadata_all_test_runs,
)

router = APIRouter(tags=["run"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    path="/runs/standalone/{test_id}",
    responses={
        201: {
            "description": (
                "A pending run was created for the test. This endpoint only "
                "enqueues the run — it does not call the model, score anything, "
                "or write back results. `status` is always `Pending` in the "
                "response; a separate, later mechanism promotes the run to "
                "`Running`, `Completed`, or `Failed` once it actually executes."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                        "status": "Pending",
                        "created_at": "2026-07-14T18:03:21.123456",
                        "test_case_id": {
                            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                        },
                    }
                }
            },
        },
        404: {
            "description": "No test exists with the given ID. No run is created.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Tests with ids ['<test_id>'] not found"
                    }
                }
            },
        },
        409: {
            "description": (
                "The test has no test types assigned. A run against a test with "
                "nothing to measure it against would be created only to sit "
                "pending forever with no way to ever produce a score, so it's "
                "rejected upfront instead. Assign at least one test type to the "
                "test (`PATCH /tests/{test_case_id}`) before creating a run for "
                "it. No run is created."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": "No test types assigned to Tests with ids "
                                  "['<test_id>']"
                    }
                }
            },
        },
    },
    status_code=201,
    response_model=StandaloneRunCreationMetadata,
)
async def run_standalone_test(
        test_id: uuid.UUID,
        session: SessionDep,
) -> StandaloneRunCreationMetadata: # pragma: no cover
    """Create a standalone, pending run for a single live test.

    A standalone run evaluates a live `TestModel` directly, outside any test
    set or test plan — the quick, ad-hoc way to run a test during authoring,
    without first snapshotting it anywhere. Unlike runs created via a test set
    or test plan, a standalone run has no "live vs. replay" concept: it
    always reads the test's current state, whatever that happens to be at the
    moment it actually executes.

    Two guards run before the run is created:
    - The test must exist (404).
    - The test must have at least one test type assigned (409) — otherwise
      the run would have nothing to be scored against, ever.

    This endpoint only creates the run record — it does not execute anything.
    Exactly one `TestRunModel` row is created regardless of how many test
    types are assigned to the test.

    Returns the new run's ID, status (always `Pending` at creation), creation
    timestamp, and the ID of the test it was created for.
    """
    return await create_new_standalone_run(test_id, session)


@router.post(
    path="/runs/test-sets/{test_set_id}",
    responses={
        201: {
            "description": (
                "A live execution was triggered: one pending run was created "
                "per entry currently in the test set, all grouped under a "
                "single new test set execution. This endpoint only enqueues "
                "the runs — it does not call the model, score anything, or "
                "write back results. Every created run's `status` is "
                "`Pending`; a separate, later mechanism promotes each one to "
                "`Running`, `Completed`, or `Failed` once it actually executes."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "id": "e5f6a7b8-c9d0-1234-ef56-7890abcdef12",
                        "created_at": "2026-07-15T16:44:30.163355",
                        "test_set_id": {
                            "id": "4e86003a-9e28-4c93-a08e-f99c6acbaab6"
                        },
                        "run_count": 2,
                    }
                }
            },
        },
        404: {
            "description": "No test set exists with the given ID. Nothing is created.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test set with ID '<test_set_id>' not found"
                    }
                }
            },
        },
        409: {
            "description": (
                "One of two things: the test set has no entries, or at least "
                "one entry in the test set has no test types assigned. A live "
                "execution with nothing to fan out over would create a test "
                "set execution record with zero runs — indistinguishable from "
                "a successful no-op — so an empty test set is rejected "
                "upfront. An entry with no test types assigned would produce "
                "a run that sits pending forever with no way to ever score "
                "it, so that's rejected upfront too. Add at least one test to "
                "the set (`POST /test-sets/{test_set_id}/entries`) or assign "
                "at least one test type to the offending entries before "
                "triggering a live run. Nothing is created in either case. "
                "The two response examples below show each distinct failure."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "no_entries": {
                            "summary": "Test set has no entries",
                            "value": {
                                "detail": "No Test Set Entries found in Test set with "
                                          "ID '<test_set_id>'"
                            },
                        },
                        "entries_missing_test_types": {
                            "summary": "One or more entries have no test types assigned",
                            "value": {
                                "detail": "No test types assigned to Test Set Entries "
                                          "with ids ['<test_set_entry_id>']"
                            },
                        },
                    }
                }
            },
        },
    },
    status_code=201,
    response_model=TestSetLiveRunCreationMetadata,
)
async def run_live_test_set_entries(
        test_set_id: uuid.UUID,
        session: SessionDep,
) -> TestSetLiveRunCreationMetadata: # pragma: no cover
    """Trigger a live execution of a test set, creating one pending run per entry.

    "Live" means the fan-out is over whatever entries the test set currently
    has, right now — not a fixed historical scope. Add or remove entries
    between calls and the next live run picks up whatever the set currently
    contains. This is distinct from replaying a specific past execution,
    which re-targets the exact same entries that
    execution used regardless of the set's current membership, for
    apples-to-apples comparison against a fixed benchmark.

    Three guards run before anything is created:
    - The test set must exist (404).
    - The test set must have at least one entry (409) — otherwise this call
      would create an execution record with zero runs, which is
      indistinguishable from success to the caller and almost certainly not
      what was intended.
    - Every entry in the test set must have at least one test type assigned
      (409) — otherwise the run created for that entry would sit pending
      forever with no way to ever produce a score.

    Creates one test set execution record (the trigger-event grouping every
    run this call produces) and one pending run per entry, all sharing that
    same execution. This endpoint only creates those records — it does not
    execute anything itself.

    Returns the new execution's ID, creation timestamp, the test set it
    targeted, and the number of runs created (equal to the number of entries
    the test set had at the moment this was triggered).
    """
    return await create_new_live_test_set_run(test_set_id, session)


@router.post(
    path="/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}",
    responses={
        201: {
            "description": (
                "A replay was triggered: one pending run was created for every "
                "entry the referenced past execution ran, all grouped under a "
                "single new test set execution. The entries targeted are the "
                "exact same ones that execution used, regardless of what the "
                "test set currently contains — entries added or removed since "
                "have no effect, and an entry that has since been unlinked "
                "from the set is still included, since its content stays "
                "frozen either way. This endpoint only enqueues the runs — it "
                "does not call the model, score anything, or write back "
                "results. Every created run's `status` is `Pending`; a "
                "separate, later mechanism promotes each one to `Running`, "
                "`Completed`, or `Failed` once it actually executes."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "id": "f6a7b8c9-d0e1-2345-fa67-890abcdef123",
                        "created_at": "2026-07-15T17:12:08.947213",
                        "test_set_id": {
                            "id": "4e86003a-9e28-4c93-a08e-f99c6acbaab6"
                        },
                        "run_count": 2,
                        "replayed_execution_id": {
                            "id": "e5f6a7b8-c9d0-1234-ef56-7890abcdef12"
                        },
                    }
                }
            },
        },
        404: {
            "description": (
                "One of three things: no test set exists with the given ID; "
                "no test set execution exists with the given ID; or the "
                "execution exists but belongs to a different test set than "
                "the one in the path — replaying execution X of test set A "
                "through test set B's URL is rejected rather than silently "
                "allowed. Nothing is created in any case. The three response "
                "examples below show each distinct failure."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "test_set_not_found": {
                            "summary": "Test set does not exist",
                            "value": {
                                "detail": "Test set with ID '<test_set_id>' not found"
                            },
                        },
                        "execution_not_found": {
                            "summary": "Test set execution does not exist",
                            "value": {
                                "detail": "Test set execution with ID "
                                          "'<test_set_execution_id>' does not exist"
                            },
                        },
                        "execution_not_linked_to_test_set": {
                            "summary": "Execution belongs to a different test set",
                            "value": {
                                "detail": "Test set execution with ID "
                                          "'<test_set_execution_id>' not linked to "
                                          "test set with ID '<test_set_id>'"
                            },
                        },
                    }
                }
            },
        },
        409: {
            "description": (
                "The referenced execution has zero runs to replay. Nothing "
                "is created. This is expected to be unreachable through "
                "normal use today — every execution that can currently exist "
                "was itself created with at least one run — but is kept as a "
                "guard against a future admin-only run-deletion feature "
                "leaving an execution with zero runs behind for a later "
                "replay to hit."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test set execution with ID "
                                  "'<test_set_execution_id>' has no test set entries"
                    }
                }
            },
        },
    },
    status_code=201,
    response_model=TestSetReplayedExecutionCreationMetadata,
)
async def replay_previous_test_set_execution(
        test_set_id: uuid.UUID,
        test_set_execution_id: uuid.UUID,
        session: SessionDep,
) -> TestSetReplayedExecutionCreationMetadata: # pragma: no cover
    """Replay a past test set execution, creating one pending run per original entry.

    "Replay" means the fan-out targets the exact same test set entries the
    referenced execution ran, not whatever the test set currently contains.
    This is the counterpart to triggering a live execution, which always
    fans out over current membership instead — replay exists for
    apples-to-apples comparison against a fixed historical scope (e.g. "did
    the model regress against exactly what was tested last time"), which a
    live re-run can't guarantee once the set's entries have changed.

    Three guards run before anything is created:
    - The test set must exist (404).
    - The referenced test set execution must exist (404).
    - That execution must belong to this test set (404) — prevents
      replaying execution X of test set A through test set B's URL.
    - The execution must have at least one run to replay (409) — expected
      to be unreachable today, kept as a guard for a future run-deletion
      feature.

    Creates one new test set execution record (with `replayed_execution_id`
    set to the execution being replayed, marking it as a replay rather than
    a live run) and one pending run per original entry, all sharing that new
    execution. This endpoint only creates those records — it does not
    execute anything itself.

    Returns the new execution's ID, creation timestamp, the test set it
    targeted, the number of runs created (always equal to the number of
    runs the replayed execution had), and the ID of the execution it
    replayed.
    """
    return await create_new_replay_test_set_run(test_set_id, test_set_execution_id, session)


@router.post(
    path="/runs/test-plans/{test_plan_id}",
    responses={
        201: {
            "description": (
                "A live execution was triggered: one pending run was created "
                "per entry across every test set currently linked to the "
                "plan, all grouped under a single new test plan execution. "
                "This endpoint only enqueues the runs — it does not call the "
                "model, score anything, or write back results. Every created "
                "run's `status` is `Pending`; a separate, later mechanism "
                "promotes each one to `Running`, `Completed`, or `Failed` "
                "once it actually executes."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "id": "a2b3c4d5-e6f7-8901-ab23-456789abcdef",
                        "created_at": "2026-07-17T09:21:44.512873",
                        "test_plan_id": {
                            "id": "7c1d2e3f-4a5b-6c7d-8e9f-0123456789ab"
                        },
                        "run_count": 5,
                    }
                }
            },
        },
        404: {
            "description": "No test plan exists with the given ID. Nothing is created.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test plan with ID '<test_plan_id>' not found"
                    }
                }
            },
        },
        409: {
            "description": (
                "One of three things: the test plan has no linked test sets; "
                "one of its linked test sets has no entries; or at least one "
                "entry across those test sets has no test types assigned. A "
                "live execution with nothing to fan out over would create a "
                "test plan execution record with zero runs — indistinguishable "
                "from a successful no-op — so an empty plan or an empty linked "
                "test set is rejected upfront. An entry with no test types "
                "assigned would produce a run that sits pending forever with "
                "no way to ever score it, so that's rejected upfront too. "
                "Link at least one test set to the plan (`POST "
                "/test-plans/{test_plan_id}/entries`), add at least one entry "
                "to the offending test set(s) (`POST "
                "/test-sets/{test_set_id}/entries`), or assign at least one "
                "test type to the offending entries before triggering a live "
                "run. Nothing is created in any case. The three response "
                "examples below show each distinct failure."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "no_linked_test_sets": {
                            "summary": "Test plan has no linked test sets",
                            "value": {
                                "detail": "Test plan with ID '<test_plan_id>' "
                                          "has no linked test sets"
                            },
                        },
                        "linked_test_set_has_no_entries": {
                            "summary": "One or more linked test sets have no entries",
                            "value": {
                                "detail": "No Test Set Entries found in Test "
                                          "sets with IDs ['<test_set_id>']"
                            },
                        },
                        "entries_missing_test_types": {
                            "summary": "One or more entries have no test types assigned",
                            "value": {
                                "detail": "No test types assigned to Test Set "
                                          "Entries with ids "
                                          "['<test_set_entry_id>']"
                            },
                        },
                    }
                }
            },
        },
    },
    status_code=201,
    response_model=TestPlanLiveRunCreationMetadata,
)
async def run_live_test_plan_entries(
        test_plan_id: uuid.UUID,
        session: SessionDep,
) -> TestPlanLiveRunCreationMetadata: # pragma: no cover
    """Trigger a live execution of a test plan, creating one pending run per
    entry across all of its linked test sets.

    "Live" means the fan-out is over whichever test sets are currently
    linked to the plan, and whatever entries those sets currently contain
    — not a fixed historical scope. Link or unlink test sets, or add or
    remove entries, between calls and the next live run picks up whatever
    the plan's scope currently resolves to. This is the plan-level
    counterpart to triggering a live test set execution, one layer up: it
    fans out across every linked test set's entries in a single execution
    instead of a single set's own entries.

    Four guards run before anything is created:
    - The test plan must exist (404).
    - The test plan must have at least one linked test set (409) —
      otherwise this call would create an execution record with zero runs,
      which is indistinguishable from success to the caller and almost
      certainly not what was intended.
    - Every linked test set must have at least one entry (409) — same
      reasoning, one level down.
    - Every entry across all linked test sets must have at least one test
      type assigned (409) — otherwise the run created for that entry would
      sit pending forever with no way to ever produce a score.

    Creates one test plan execution record (the trigger-event grouping
    every run this call produces) and one pending run per entry across all
    linked test sets, all sharing that same execution. This endpoint only
    creates those records — it does not execute anything itself.

    Returns the new execution's ID, creation timestamp, the test plan it
    targeted, and the number of runs created (equal to the number of
    entries across all of the plan's linked test sets at the moment this
    was triggered).
    """
    return await create_new_live_test_plan_run(test_plan_id, session)


@router.post(
    path="/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}",
    responses={
        201: {
            "description": (
                "A replay was triggered: one pending run was created for every "
                "entry the referenced past execution ran, all grouped under a "
                "single new test plan execution. The entries targeted are the "
                "exact same ones that execution used, regardless of what test "
                "sets are currently linked to the plan — test sets linked or "
                "unlinked since have no effect, and an entry whose test set "
                "has since been unlinked from the plan is still included, "
                "since its content stays frozen either way. This endpoint "
                "only enqueues the runs — it does not call the model, score "
                "anything, or write back results. Every created run's "
                "`status` is `Pending`; a separate, later mechanism promotes "
                "each one to `Running`, `Completed`, or `Failed` once it "
                "actually executes."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "id": "b3c4d5e6-f7a8-9012-bc34-56789abcdef0",
                        "created_at": "2026-07-18T11:05:52.284917",
                        "test_plan_id": {
                            "id": "7c1d2e3f-4a5b-6c7d-8e9f-0123456789ab"
                        },
                        "run_count": 5,
                        "replayed_execution_id": {
                            "id": "a2b3c4d5-e6f7-8901-ab23-456789abcdef"
                        },
                    }
                }
            },
        },
        404: {
            "description": (
                "One of three things: no test plan exists with the given ID; "
                "no test plan execution exists with the given ID; or the "
                "execution exists but belongs to a different test plan than "
                "the one in the path — replaying execution X of test plan A "
                "through test plan B's URL is rejected rather than silently "
                "allowed. Nothing is created in any case. The three response "
                "examples below show each distinct failure."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "test_plan_not_found": {
                            "summary": "Test plan does not exist",
                            "value": {
                                "detail": "Test plan with ID '<test_plan_id>' not found"
                            },
                        },
                        "execution_not_found": {
                            "summary": "Test plan execution does not exist",
                            "value": {
                                "detail": "Test plan execution with ID "
                                          "'<test_plan_execution_id>' does not exist"
                            },
                        },
                        "execution_not_linked_to_test_plan": {
                            "summary": "Execution belongs to a different test plan",
                            "value": {
                                "detail": "Test plan execution with ID "
                                          "'<test_plan_execution_id>' not linked to "
                                          "test plan with ID '<test_plan_id>'"
                            },
                        },
                    }
                }
            },
        },
        409: {
            "description": (
                "The referenced execution has zero runs to replay. Nothing "
                "is created. This is expected to be unreachable through "
                "normal use today — every execution that can currently exist "
                "was itself created with at least one run — but is kept as a "
                "guard against a future admin-only run-deletion feature "
                "leaving an execution with zero runs behind for a later "
                "replay to hit."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test plan execution with ID "
                                  "'<test_plan_execution_id>' has no test set entries"
                    }
                }
            },
        },
    },
    status_code=201,
    response_model=TestPlanReplayedExecutionCreationMetadata,
)
async def replay_previous_test_plan_execution(
        test_plan_id: uuid.UUID,
        test_plan_execution_id: uuid.UUID,
        session: SessionDep,
) -> TestPlanReplayedExecutionCreationMetadata: # pragma: no cover
    """Replay a past test plan execution, creating one pending run per original entry.

    "Replay" means the fan-out targets the exact same test set entries the
    referenced execution ran, not whatever test sets are currently linked to
    the plan. This is the counterpart to triggering a live execution, which
    always fans out over the plan's current linked test sets instead —
    replay exists for apples-to-apples comparison against a fixed
    historical scope (e.g. "did the model regress against exactly what was
    tested last time"), which a live re-run can't guarantee once the plan's
    linked test sets have changed.

    Four guards run before anything is created:
    - The test plan must exist (404).
    - The referenced test plan execution must exist (404).
    - That execution must belong to this test plan (404) — prevents
      replaying execution X of test plan A through test plan B's URL.
    - The execution must have at least one run to replay (409).

    Creates one new test plan execution record (with `replayed_execution_id`
    set to the execution being replayed, marking it as a replay rather than
    a live run) and one pending run per original test set entry,
    all sharing that new execution. This endpoint only creates those records —
    it does not execute anything itself.

    Returns the new execution's ID, creation timestamp, the test plan it
    targeted, the number of runs created (always equal to the number of
    runs the replayed execution had), and the ID of the execution it
    replayed.
    """
    return await create_new_replay_test_plan_run(test_plan_id, test_plan_execution_id, session)


@router.get(
    path="/runs/standalone/{test_id}/test-runs",
    summary="List standalone runs created for a test",
    responses={
        200: {
            "description": "A paginated list of standalone runs created for the test.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                                "status": "Completed",
                                "created_at": "2026-07-14T18:03:21.123456",
                                "test_case_id": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                },
                            },
                            {
                                "id": "d4e5f6a7-b8c9-0123-def4-56789012345a",
                                "status": "Pending",
                                "created_at": "2026-07-15T09:12:47.884213",
                                "test_case_id": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                },
                            },
                        ],
                    }
                }
            },
        },
        404: {
            "description": "No test exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Tests with ids ['<test_id>'] not found"
                    }
                }
            },
        },
    },
    response_model=PaginatedStandaloneRunCreationMetadata,
)
async def get_standalone_run_metadata(
        test_id: uuid.UUID,
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(default=100, description="Maximum number of records to "
                                                    "return for pagination.")
) -> PaginatedStandaloneRunCreationMetadata: # pragma: no cover
    """List every standalone run ever created for a test, newest first.

    This only covers runs created directly against the live test via
    `POST /runs/standalone/{test_id}` — it does not include runs created as
    part of a test set or test plan execution, even if that execution
    happened to target this same test through one of its entries.

    One guard runs before the list is fetched:
    - The test must exist (404).

    Returns a paginated list of each run's `id`, `status`, `created_at`, and
    `test_case_id`, ordered by `created_at` descending (ties broken by `id`
    descending), plus the usual `total`, `offset`, and `limit`.
    """
    return await get_standalone_run_metadata_all_test_runs(test_id, session, offset, limit)


@router.get(
    path="/runs/standalone/{test_id}/test-runs/{test_run_id}",
    summary="Get full details for a single standalone run",
    responses={
        200: {
            "description": (
                "Full details for the standalone run, including its "
                "post-execution results. `scores`, `error`, and "
                "`executed_at` are null until the run reaches a terminal "
                "status (`Completed` or `Failed`) — this example shows a "
                "completed run with scores populated."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                        "status": "Completed",
                        "created_at": "2026-07-14T18:03:21.123456",
                        "test_case_id": {
                            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                        },
                        "scores": {
                            "exact_match": 1.0,
                            "bleu": 0.42
                        },
                        "error": None,
                        "executed_at": "2026-07-14T18:03:24.981022",
                    }
                }
            },
        },
        404: {
            "description": (
                "One of three things: no test exists with the given ID; "
                "no test run exists with the given ID; or the run exists "
                "but belongs to a different test than the one in the path "
                "— reading run X of test A through test B's URL is "
                "rejected rather than silently allowed. The three response "
                "examples below show each distinct failure."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "test_not_found": {
                            "summary": "Test does not exist",
                            "value": {
                                "detail": "Test with id <test_id> not found"
                            },
                        },
                        "test_run_not_found": {
                            "summary": "Test run does not exist",
                            "value": {
                                "detail": "Test run with ID '<test_run_id>' "
                                          "does not exist"
                            },
                        },
                        "test_run_not_linked_to_test": {
                            "summary": "Test run belongs to a different test",
                            "value": {
                                "detail": "Test run with ID '<test_run_id>' "
                                          "not linked to test with ID '<test_id>'"
                            },
                        },
                    }
                }
            },
        },
    },
    response_model=StandaloneRunDetails,
)
async def get_standalone_run_details(
        test_id: uuid.UUID,
        test_run_id: uuid.UUID,
        session: SessionDep,
) -> StandaloneRunDetails: # pragma: no cover
    """Retrieve full details for a single standalone run, including its results.

    Covers only runs created via `POST /runs/standalone/{test_id}` — not
    runs created as part of a test set or test plan execution, even if one
    happened to target this same test through one of its entries.

    Three guards run before the details are fetched:
    - The test must exist (404).
    - The test run must exist (404).
    - The test run must belong to this test (404) — prevents reading run X
      of test A through test B's URL.

    Returns the run's `id`, `status`, `created_at`, and `test_case_id`,
    plus `scores`, `error`, and `executed_at` — the latter three are null
    until the run reaches a terminal status (`Completed` or `Failed`), and
    `scores`/`error` are mutually exclusive even then: a run either scores
    successfully or fails, never both.
    """
    return await get_run_details_by_test_and_run_id(test_id, test_run_id, session)


@router.get(
    path="/runs/test-sets/{test_set_id}/executions",
    summary="List past executions of a test set",
    responses={
        200: {
            "description": "A paginated list of executions triggered for the test set.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                                "created_at": "2026-07-14T18:03:21.123456",
                                "test_set_id": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                },
                                "run_count": 5,
                                "replayed_execution_id": None,
                            },
                            {
                                "id": "d4e5f6a7-b8c9-0123-def4-56789012345a",
                                "created_at": "2026-07-15T09:12:47.884213",
                                "test_set_id": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                },
                                "run_count": 3,
                                "replayed_execution_id": {
                                    "id": "c3d4e5f6-a7b8-9012-cdef-123456789012"
                                },
                            },
                        ],
                    }
                }
            },
        },
        404: {
            "description": "No test set exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test set with ID '<test_set_id>' not found"
                    }
                }
            },
        },
    },
    response_model=PaginatedTestSetRunCreationMetadata,
)
async def get_test_sets_runs_metadata(
        test_set_id: uuid.UUID,
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(
            default=100, description="Maximum number of records to return for pagination."
        ),
) -> PaginatedTestSetRunCreationMetadata: # pragma: no cover
    """List every execution ever triggered for a test set, newest first.

    Covers both live fan-outs (`POST /runs/test-sets/{test_set_id}`) and
    replays (`POST /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}`)
    — a replay's item has `replayed_execution_id` set to the execution it
    replayed, a live fan-out's is null.

    One guard runs before the list is fetched:
    - The test set must exist (404).

    Returns a paginated list of each execution's `id`, `created_at`,
    `test_set_id`, `run_count` (the number of `TestRunModel` rows that
    execution produced), and `replayed_execution_id`, ordered by
    `created_at` descending (ties broken by `id` descending), plus the
    usual `total`, `offset`, and `limit`.
    """
    return await get_test_set_run_metadata_all_test_runs(test_set_id, session, offset, limit)
