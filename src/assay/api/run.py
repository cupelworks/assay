import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import (
    StandaloneRunCreationMetadata,
    TestSetLiveRunCreationMetadata,
    TestSetReplayedExecutionCreationMetadata,
)
from assay.services import (
    create_new_live_test_set_run,
    create_new_replay_test_set_run,
    create_new_standalone_run,
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
                        "detail": "No test types assigned to Test with id "
                                  "<test_id>"
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
                "The test set has no entries. A live execution with nothing to "
                "fan out over would create a test set execution record with "
                "zero runs — indistinguishable from a successful no-op — so "
                "it's rejected upfront instead. Add at least one test to the "
                "set (`POST /test-sets/{test_set_id}/entries`) before "
                "triggering a live run. Nothing is created."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": "No Test Set Entries found in Test set with "
                                  "ID '<test_set_id>'"
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

    Two guards run before anything is created:
    - The test set must exist (404).
    - The test set must have at least one entry (409) — otherwise this call
      would create an execution record with zero runs, which is
      indistinguishable from success to the caller and almost certainly not
      what was intended.

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
