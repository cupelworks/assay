import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import (
    PaginatedTestSetExecutionMetadata,
    PaginatedTestSetExecutionRunMetadata,
    TestSetExecutionRunDetails,
    TestSetLiveRunCreationMetadata,
    TestSetReplayedExecutionCreationMetadata,
)
from assay.services import (
    create_new_live_test_set_run,
    create_new_replay_test_set_run,
    get_run_details_by_test_set_execution_and_run_id,
    get_test_set_execution_metadata_all_executions,
    get_test_set_execution_run_metadata_all_runs,
)

router = APIRouter(tags=["run (test-set)"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    path="/runs/test-sets/{test_set_id}",
    responses={
        201: {
            "description": (
                "A live execution was triggered: one pending run was created "
                "per entry currently in the test set, all grouped under a "
                "single new test set execution, and each one dispatched for "
                "execution. This endpoint does not call the model, score "
                "anything, or write back results itself — every created "
                "run's `status` is `Pending`, since the worker that does all "
                "of that runs separately, after this response is returned. "
                "Dispatch is best-effort per run: a run whose dispatch fails "
                "(e.g. the broker is unreachable) simply stays `Pending`. "
                "Once picked up, the worker promotes each run to `Running`, "
                "then a terminal outcome (`Green`, `Amber`, `Red`, or "
                "`NotRan`)."
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
    same execution. This endpoint creates those records and dispatches each
    new run for execution — it does not execute anything itself; that
    happens in the worker process, once it picks up each dispatched task.

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
                "frozen either way, and each new run is dispatched for "
                "execution. This endpoint does not call the model, score "
                "anything, or write back results itself — every created "
                "run's `status` is `Pending`, since the worker that does all "
                "of that runs separately, after this response is returned. "
                "Dispatch is best-effort per run: a run whose dispatch fails "
                "(e.g. the broker is unreachable) simply stays `Pending`. "
                "Once picked up, the worker promotes each run to `Running`, "
                "then a terminal outcome (`Green`, `Amber`, `Red`, or "
                "`NotRan`)."
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
    execution. This endpoint creates those records and dispatches each new
    run for execution — it does not execute anything itself; that happens
    in the worker process, once it picks up each dispatched task.

    Returns the new execution's ID, creation timestamp, the test set it
    targeted, the number of runs created (always equal to the number of
    runs the replayed execution had), and the ID of the execution it
    replayed.
    """
    return await create_new_replay_test_set_run(test_set_id, test_set_execution_id, session)


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
    response_model=PaginatedTestSetExecutionMetadata,
)
async def get_test_set_execution_metadata(
        test_set_id: uuid.UUID,
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(
            default=100, description="Maximum number of records to return for pagination."
        ),
) -> PaginatedTestSetExecutionMetadata: # pragma: no cover
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
    return await get_test_set_execution_metadata_all_executions(test_set_id, session, offset, limit)


@router.get(
    path="/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs",
    summary="List runs produced by a specific test set execution",
    responses={
        200: {
            "description": "A paginated list of runs produced by this test set execution.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                                "status": "Green",
                                "created_at": "2026-07-15T16:44:30.163355",
                                "test_set_entry_id": {
                                    "id": "d4e5f6a7-b8c9-0123-def4-56789012345a"
                                },
                                "test_set_execution_id": {
                                    "id": "e5f6a7b8-c9d0-1234-ef56-7890abcdef12"
                                },
                            },
                            {
                                "id": "f6a7b8c9-d0e1-2345-fa67-890abcdef123",
                                "status": "Pending",
                                "created_at": "2026-07-15T16:44:30.163355",
                                "test_set_entry_id": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                },
                                "test_set_execution_id": {
                                    "id": "e5f6a7b8-c9d0-1234-ef56-7890abcdef12"
                                },
                            },
                        ],
                    }
                }
            },
        },
        404: {
            "description": (
                "One of three things: no test set exists with the given ID; "
                "no test set execution exists with the given ID; or the "
                "execution exists but belongs to a different test set than "
                "the one in the path — reading the runs of execution X of "
                "test set A through test set B's URL is rejected rather "
                "than silently allowed. The three response examples below "
                "show each distinct failure."
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
    },
    response_model=PaginatedTestSetExecutionRunMetadata,
)
async def get_test_set_execution_run_metadata(
        test_set_id: uuid.UUID,
        test_set_execution_id: uuid.UUID,
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(
            default=100, description="Maximum number of records to return for pagination."),
) -> PaginatedTestSetExecutionRunMetadata: # pragma: no cover
    """List every run produced by a specific test set execution, newest first.

    Scoped to exactly one execution — covers only the runs
    `test_set_execution_id` itself produced, not the test set's other past
    executions or its current live entries.

    Three guards run before the list is fetched:
    - The test set must exist (404).
    - The referenced test set execution must exist (404).
    - That execution must belong to this test set (404) — prevents reading
      the runs of execution X of test set A through test set B's URL.

    Returns a paginated list of each run's `id`, `status`, `created_at`,
    `test_set_entry_id` (which entry it ran), and `test_set_execution_id`,
    ordered by `created_at` descending (ties broken by `id` descending),
    plus the usual `total`, `offset`, and `limit`.
    """
    return await get_test_set_execution_run_metadata_all_runs(
        test_set_id, test_set_execution_id, session, offset, limit
    )


@router.get(
    path="/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}",
    summary="Get full details for a single run produced by a test set execution",
    responses={
        200: {
            "description": (
                "Full details for the run, including the snapshotted test set "
                "entry it ran against and its post-execution results. `results`, "
                "`error`, and `executed_at` are null until the run reaches a "
                "terminal status (`Green`, `Amber`, `Red`, or `NotRan`) — this "
                "example shows a run where every assigned test type passed."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                        "status": "Green",
                        "created_at": "2026-07-15T16:44:30.163355",
                        "test_set_entry_id": {
                            "id": "d4e5f6a7-b8c9-0123-def4-56789012345a"
                        },
                        "test_set_execution_id": {
                            "id": "e5f6a7b8-c9d0-1234-ef56-7890abcdef12"
                        },
                        "results": {
                            "Exact Match": {
                                "passed": True, "score": None, "detail": None,
                                "engine": "exact_match",
                                "engine_settings": {"trim": True, "case_sensitive": True},
                            },
                            "BLEU": {
                                "passed": True, "score": 42.0, "detail": None,
                                "engine": "bleu", "engine_settings": {"smoothing": True},
                            },
                        },
                        "error": None,
                        "evaluated_output": "Go to Settings → Security and choose Reset password.",
                        "output_source": "recorded",
                        "executed_at": "2026-07-15T16:44:33.981022",
                        "test_case_id": {
                            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                        },
                        "name": "greets the user by name",
                        "input": "Say hello to Alice.",
                        "expected_output": "Hello, Alice!",
                        "model_output": "Hello, Alice!",
                        "test_type_assignments": [
                            {"name": "Exact Match", "config": None},
                            {"name": "BLEU", "config": {"threshold": "0.6"}},
                        ],
                        "test_case_snapshot_at": {
                            "snapshot_at": "2026-07-15T16:40:02.552210"
                        },
                        "test_set_id": {
                            "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901"
                        },
                    }
                }
            },
        },
        404: {
            "description": (
                "One of five things: no test set exists with the given ID; "
                "no test set execution exists with the given ID; the "
                "execution exists but belongs to a different test set than "
                "the one in the path; no test run exists with the given ID; "
                "or the run exists but belongs to a different execution than "
                "the one in the path — reading run X of execution Y through "
                "execution Z's URL is rejected rather than silently allowed. "
                "The five response examples below show each distinct failure."
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
                        "test_run_not_found": {
                            "summary": "Test run does not exist",
                            "value": {
                                "detail": "Test run with ID '<test_run_id>' "
                                          "does not exist"
                            },
                        },
                        "test_run_not_linked_to_execution": {
                            "summary": "Test run belongs to a different execution",
                            "value": {
                                "detail": "Test run with ID '<test_run_id>' not linked "
                                          "to test set execution with ID "
                                          "'<test_set_execution_id>'"
                            },
                        },
                    }
                }
            },
        },
    },
    response_model=TestSetExecutionRunDetails,
)
async def get_test_set_execution_run_details(
        test_set_id: uuid.UUID,
        test_set_execution_id: uuid.UUID,
        test_run_id: uuid.UUID,
        session: SessionDep,
) -> TestSetExecutionRunDetails: # pragma: no cover
    """Retrieve full details for a single run produced by a test set execution,
    including the snapshotted test set entry it ran against.

    Covers only runs produced by a test set execution (live or replay,
    `POST /runs/test-sets/{test_set_id}` or its replay counterpart) — not
    standalone runs, and not runs produced by a test plan execution, even
    if one happened to target this same test set entry.

    Five guards run before the details are fetched:
    - The test set must exist (404).
    - The referenced test set execution must exist (404).
    - That execution must belong to this test set (404) — prevents reading
      execution X of test set A through test set B's URL.
    - The test run must exist (404).
    - That run must belong to this execution (404) — prevents reading run X
      of execution Y through execution Z's URL.

    Returns the run's `id`, `status`, `created_at`, `test_set_entry_id`, and
    `test_set_execution_id`, plus `results`, `error`, and `executed_at` — the
    latter three are null until the run reaches a terminal status
    (`Green`, `Amber`, `Red`, or `NotRan`), and `results`/`error` are
    mutually exclusive even then: `error` is only ever set for `NotRan`,
    `results` for the other three.
    Also returns the snapshotted test set entry the run executed against —
    `test_case_id` (the live test it was originally snapshotted from),
    `name`, `input`, `expected_output`, `model_output`, `test_type_assignments`,
    and `test_case_snapshot_at` — frozen at the moment the entry was added
    to the test set, and never updated by later edits to the live test.
    This stays reachable even if the entry has since been unlinked from
    the test set (`PATCH /test-sets/{test_set_id}/entries`) — the five
    guards above already establish that this run belongs to this test
    set's history, independent of the entry's current membership.
    """
    return await get_run_details_by_test_set_execution_and_run_id(
        test_set_id, test_set_execution_id, test_run_id, session
    )
