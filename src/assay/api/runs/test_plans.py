import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.api.runs._batch_filter import BatchFilter
from assay.api.runs._execution_example import execution_details
from assay.db import get_session
from assay.schemas import (
    ExecutionRunSort,
    PaginatedTestPlanExecutionMetadata,
    PaginatedTestPlanExecutionRunMetadata,
    TestPlanExecutionDetails,
    TestPlanExecutionRunDetails,
    TestPlanLiveRunCreationMetadata,
    TestPlanReplayedExecutionCreationMetadata,
)
from assay.services import (
    create_new_live_test_plan_run,
    create_new_replay_test_plan_run,
    get_run_details_by_test_plan_execution_and_run_id,
    get_test_plan_execution_details,
    get_test_plan_execution_metadata_all_executions,
    get_test_plan_execution_run_metadata_all_runs,
)

router = APIRouter(tags=["run (test plan)"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


_EXECUTION_404 = {
    "description": (
        "One of three things: no test plan exists with the given ID; "
        "no test plan execution exists with the given ID; or the "
        "execution exists but belongs to a different test plan than "
        "the one in the path — reading the runs of execution X of "
        "test plan A through test plan B's URL is rejected rather "
        "than silently allowed. The three response examples below "
        "show each distinct failure."
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
}


_RUN_DETAIL_RECORDED = {
    "id": "f1a2b3c4-d5e6-7890-fabc-234567890123",
    "status": "Green",
    "created_at": "2026-07-20T09:12:04.221310",
    "batch_id": None,
    "batch_index": None,
    "test_set_entry_id": {
        "id": "a2b3c4d5-e6f7-8901-abcd-345678901234"
    },
    "test_plan_execution_id": {
        "id": "b3c4d5e6-f7a8-9012-bcde-456789012345"
    },
    # keyed by label, in label order; each result says which type it ran
    "results": {
        "BLEU": {
            "passed": True, "score": 100.0, "detail": None, "test_type": "BLEU",
            "engine": "bleu", "engine_settings": {"smooth_method": "exp", "lowercase": False},
        },
        "Exact Match": {
            "passed": True, "score": None, "detail": None, "test_type": "Exact Match",
            "engine": "exact_match",
            "engine_settings": {"trim": True, "case_sensitive": True,
                                "normalize_lookalikes": True},
        },
    },
    "error": None,
    "evaluated_output": "Hello, Alice!",
    "output_source": "recorded",
    "executed_at": "2026-07-20T09:12:08.554021",
    "test_case_id": {
        "id": "c4d5e6f7-a8b9-0123-cdef-567890123456"
    },
    "name": "greets the user by name",
    "input": "Say hello to Alice.",
    "expected_output": "Hello, Alice!",
    "model_output": "Hello, Alice!",
    "test_type_assignments": [
        {"name": "BLEU", "label": "BLEU", "config": {"threshold": "20"}},
        {"name": "Exact Match", "label": "Exact Match", "config": None},
    ],
    "test_case_snapshot_at": {
        "snapshot_at": "2026-07-20T09:10:41.117903"
    },
    "test_set_id": {
        "id": "d5e6f7a8-b9c0-1234-defa-678901234567"
    },
    "test_plan_id": {
        "id": "e6f7a8b9-c0d1-2345-efab-789012345678"
    },
}

# The same test, answered by the application under test: the whole reply is
# kept, the default answer is its output object as JSON text, and both checks
# read the greeting inside it (answer_path).
_RUN_DETAIL_FROM_APPLICATION = {
    **_RUN_DETAIL_RECORDED,
    "results": {
        label: {**result, "answer_path": "$.output.greeting"}
        for label, result in _RUN_DETAIL_RECORDED["results"].items()
    },
    "evaluated_output": '{"greeting": "Hello, Alice!"}',
    "output_source": "application",
    "application_reply": {
        "output": {"greeting": "Hello, Alice!"},
        "model": "claude-sonnet-5-5",
        "stop_reason": "end_turn",
        "input_tokens": 812,
        "output_tokens": 64,
    },
    "model_output": None,
    "test_type_assignments": [
        {"name": "BLEU", "label": "BLEU", "config": {"threshold": "20"},
         "answer_path": "$.output.greeting"},
        {"name": "Exact Match", "label": "Exact Match", "config": None,
         "answer_path": "$.output.greeting"},
    ],
}

@router.post(
    path="/runs/test-plans/{test_plan_id}",
    responses={
        201: {
            "description": (
                "A live execution was triggered: one pending run was created "
                "per entry across every test set currently linked to the "
                "plan, all grouped under a single new test plan execution, "
                "and each one dispatched for execution. This endpoint doesn't "
                "call your application, score anything, or write back results "
                "itself — every created run's `status` is `Pending`, since "
                "the worker that does all of that runs separately, after "
                "this response is returned. Dispatch is best-effort per run: "
                "a run whose dispatch fails (e.g. the broker is unreachable) "
                "stays `Pending` until the reconciliation scan re-sends it. "
                "Once picked up, the worker promotes "
                "each run to `Running`, then a terminal outcome (`Green`, "
                "`Amber`, `Red`, or `NotRan`)."
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
    linked test sets, all sharing that same execution. This endpoint
    creates those records and dispatches each new run for execution — it
    does not execute anything itself; that happens in the worker process,
    once it picks up each dispatched task.

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
                "since its content stays frozen either way, and each new "
                "run is dispatched for execution. This endpoint doesn't "
                "call your application, score anything, or write back results "
                "itself — every created run's `status` is `Pending`, since "
                "the worker that does all of that runs separately, after "
                "this response is returned. Dispatch is best-effort per "
                "run: a run whose dispatch fails (e.g. the broker is "
                "unreachable) stays `Pending` until the reconciliation scan "
                "re-sends it. Once picked up, the "
                "worker promotes each run to `Running`, then a terminal "
                "outcome (`Green`, `Amber`, `Red`, or `NotRan`)."
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
    An entry with no recorded answer asks the application under test again,
    with the settings in effect now, so its answer — and score — can differ
    from the original run's.

    Four guards run before anything is created:
    - The test plan must exist (404).
    - The referenced test plan execution must exist (404).
    - That execution must belong to this test plan (404) — prevents
      replaying execution X of test plan A through test plan B's URL.
    - The execution must have at least one run to replay (409).

    Creates one new test plan execution record (with `replayed_execution_id`
    set to the execution being replayed, marking it as a replay rather than
    a live run) and one pending run per original test set entry,
    all sharing that new execution. This endpoint creates those records and
    dispatches each new run for execution — it does not execute anything
    itself; that happens in the worker process, once it picks up each
    dispatched task.

    Returns the new execution's ID, creation timestamp, the test plan it
    targeted, the number of runs created (always equal to the number of
    runs the replayed execution had), and the ID of the execution it
    replayed.
    """
    return await create_new_replay_test_plan_run(test_plan_id, test_plan_execution_id, session)


@router.get(
    path="/runs/test-plans/{test_plan_id}/executions",
    summary="List past executions of a test plan",
    responses={
        200: {
            "description": "A paginated list of executions triggered for the test plan.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "a2b3c4d5-e6f7-8901-ab23-456789abcdef",
                                "created_at": "2026-07-17T09:21:44.512873",
                                "test_plan_id": {
                                    "id": "7c1d2e3f-4a5b-6c7d-8e9f-0123456789ab"
                                },
                                "run_count": 5,
                                "replayed_execution_id": None,
                                "runs": {"Pending": 0, "Running": 0, "Green": 4, "Amber": 1,
                                         "Red": 0, "NotRan": 0},
                            },
                            {
                                "id": "b3c4d5e6-f7a8-9012-bc34-56789abcdef0",
                                "created_at": "2026-07-18T11:05:52.284917",
                                "test_plan_id": {
                                    "id": "7c1d2e3f-4a5b-6c7d-8e9f-0123456789ab"
                                },
                                "run_count": 5,
                                "replayed_execution_id": {
                                    "id": "a2b3c4d5-e6f7-8901-ab23-456789abcdef"
                                },
                                "runs": {"Pending": 0, "Running": 1, "Green": 2, "Amber": 0,
                                         "Red": 0, "NotRan": 0},
                            },
                        ],
                    }
                }
            },
        },
        404: {
            "description": "No test plan exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test plan with ID '<test_plan_id>' not found"
                    }
                }
            },
        },
    },
    response_model=PaginatedTestPlanExecutionMetadata,
)
async def get_test_plan_execution_metadata(
        test_plan_id: uuid.UUID,
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(
            default=100, description="Maximum number of records to return for pagination."
        ),
        batch: BatchFilter = None,
) -> PaginatedTestPlanExecutionMetadata: # pragma: no cover
    """List every execution ever triggered for a test plan, newest first.

    Covers both live fan-outs (`POST /runs/test-plans/{test_plan_id}`) and
    replays (`POST /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}`)
    — a replay's item has `replayed_execution_id` set to the execution it
    replayed, a live fan-out's is null.

    One guard runs before the list is fetched:
    - The test plan must exist (404).

    Returns a paginated list of each execution's `id`, `created_at`,
    `test_plan_id`, `run_count` (the number of `TestRunModel` rows that
    execution produced), and `replayed_execution_id`, ordered by
    `created_at` descending (ties broken by `id` descending), plus the
    usual `total`, `offset`, and `limit`.
    """
    return await get_test_plan_execution_metadata_all_executions(
        test_plan_id, session, offset, limit, batch
    )


@router.get(
    path="/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}",
    summary="Read one test plan execution: its runs counted and its checks gathered",
    responses={
        200: {"content": {"application/json": {
            "example": execution_details("test_plan_id", "Release gate")}}},
        404: _EXECUTION_404,
    },
    response_model=TestPlanExecutionDetails,
)
async def get_test_plan_execution(
        test_plan_id: uuid.UUID,
        test_plan_execution_id: uuid.UUID,
        session: SessionDep,
) -> TestPlanExecutionDetails:  # pragma: no cover
    """One execution of the test plan, read whole: when it started and what it
    replayed, its runs by status, and its checks over the runs that finished —
    how many were met, each one not met with its run and test, and each run
    that couldn't run with why. Runs still Pending or Running count only in
    `runs`. The same guards as its runs list."""
    return await get_test_plan_execution_details(test_plan_id, test_plan_execution_id, session)


@router.get(
    path="/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs",
    summary="List runs produced by a specific test plan execution",
    responses={
        200: {
            "description": "A paginated list of runs produced by this test plan execution.",
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
                                "created_at": "2026-07-18T11:05:55.163355",
                                "batch_id": None,
                                "batch_index": None,
                                "test_set_entry_id": {
                                    "id": "d4e5f6a7-b8c9-0123-def4-56789012345a"
                                },
                                "test_name": "Reset a password",
                                "checks": {"met": 1, "total": 2, "not_met": ["Regex Match"]},
                                "error": None,
                                "output_source": "recorded",
                                "test_plan_execution_id": {
                                    "id": "b3c4d5e6-f7a8-9012-bc34-56789abcdef0"
                                },
                            },
                            {
                                "id": "f6a7b8c9-d0e1-2345-fa67-890abcdef123",
                                "status": "Pending",
                                "created_at": "2026-07-18T11:05:55.163355",
                                "batch_id": None,
                                "batch_index": None,
                                "test_set_entry_id": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                },
                                "test_name": "Reset a password",
                                "checks": {"met": 1, "total": 2, "not_met": ["Regex Match"]},
                                "error": None,
                                "output_source": "recorded",
                                "test_plan_execution_id": {
                                    "id": "b3c4d5e6-f7a8-9012-bc34-56789abcdef0"
                                },
                            },
                        ],
                    }
                }
            },
        },
        404: _EXECUTION_404,
    },
    response_model=PaginatedTestPlanExecutionRunMetadata,
)
async def get_test_plan_execution_run_metadata(
        test_plan_id: uuid.UUID,
        test_plan_execution_id: uuid.UUID,
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(
            default=100, description="Maximum number of records to return for pagination."
        ),
        sort: Annotated[ExecutionRunSort, Query(
            description="`newest` (the default), `worst_first` (NotRan, Red, Amber, Green, "
                        "Running, Pending, then by test name) or `name` (by test name, "
                        "ignoring case).")] = ExecutionRunSort.newest,
) -> PaginatedTestPlanExecutionRunMetadata: # pragma: no cover
    """List every run produced by a specific test plan execution, newest first.

    Scoped to exactly one execution — covers only the runs
    `test_plan_execution_id` itself produced, not the test plan's other past
    executions or its current live entries.

    Three guards run before the list is fetched:
    - The test plan must exist (404).
    - The referenced test plan execution must exist (404).
    - That execution must belong to this test plan (404) — prevents reading
      the runs of execution X of test plan A through test plan B's URL.

    Returns a paginated list of each run's `id`, `status`, `created_at`,
    `test_set_entry_id` (which entry it ran), and `test_plan_execution_id`,
    ordered by `created_at` descending (ties broken by `id` descending),
    plus the usual `total`, `offset`, and `limit`.
    """
    return await get_test_plan_execution_run_metadata_all_runs(
        test_plan_id, test_plan_execution_id, session, offset, limit, sort
    )


@router.get(
    path="/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}",
    summary="Get full details for a single run produced by a test plan execution",
    responses={
        200: {
            "description": (
                "Full details for the run, including the snapshotted test set "
                "entry it ran against and its post-execution results. `results`, "
                "`error`, and `executed_at` are null until the run reaches a "
                "terminal status (`Green`, `Amber`, `Red`, or `NotRan`). The two "
                "examples show a run scored from the entry's recorded answer, "
                "and one scored from the application's reply, kept whole in "
                "`application_reply`, where one check reads its own part of it "
                "(`answer_path`). "
                "`test_set_id` reflects the entry's *current* test set and is "
                "null if the entry has since been unlinked from it (`PATCH "
                "/test-sets/{test_set_id}/entries`) — it does not affect "
                "whether this run's own detail is reachable, only this one "
                "field."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "recorded": {"summary": "The test's recorded answer was scored",
                                     "value": _RUN_DETAIL_RECORDED},
                        "application": {"summary": "Scored from the application's reply; one "
                                                   "check reads its own part of it",
                                        "value": _RUN_DETAIL_FROM_APPLICATION},
                    }
                }
            },
        },
        404: {
            "description": (
                "One of five things: no test plan exists with the given ID; "
                "no test plan execution exists with the given ID; the "
                "execution exists but belongs to a different test plan than "
                "the one in the path; no test run exists with the given ID; "
                "or the run exists but belongs to a different execution than "
                "the one in the path — reading run X of execution Y through "
                "execution Z's URL is rejected rather than silently allowed. "
                "The five response examples below show each distinct failure."
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
                                          "to test plan execution with ID "
                                          "'<test_plan_execution_id>'"
                            },
                        },
                    }
                }
            },
        },
    },
    response_model=TestPlanExecutionRunDetails,
)
async def get_test_plan_execution_run_details(
        test_plan_id: uuid.UUID,
        test_plan_execution_id: uuid.UUID,
        test_run_id: uuid.UUID,
        session: SessionDep,
) -> TestPlanExecutionRunDetails: # pragma: no cover
    """Retrieve full details for a single run produced by a test plan execution,
    including the snapshotted test set entry it ran against.

    Covers only runs produced by a test plan execution (live or replay,
    `POST /runs/test-plans/{test_plan_id}` or its replay counterpart) — not
    standalone runs, and not runs produced by a test set execution, even if
    one happened to target this same test set entry.

    Five guards run before the details are fetched:
    - The test plan must exist (404).
    - The referenced test plan execution must exist (404).
    - That execution must belong to this test plan (404) — prevents reading
      execution X of test plan A through test plan B's URL.
    - The test run must exist (404).
    - That run must belong to this execution (404) — prevents reading run X
      of execution Y through execution Z's URL.

    Returns the run's `id`, `status`, `created_at`, `test_set_entry_id`, and
    `test_plan_execution_id`, plus `results`, `error`, and `executed_at` — the
    latter three are null until the run reaches a terminal status
    (`Green`, `Amber`, `Red`, or `NotRan`), and `results`/`error` are
    mutually exclusive even then: `error` is only ever set for `NotRan`,
    `results` for the other three.

    Also returns what was scored: `evaluated_output`, the answer every check
    reads by default — the test's recorded `model_output`, or, when it has
    none, the application's answer at the settings' output path (JSON text
    for a structured answer, `""` when the application answered with
    nothing) — and `output_source` (`recorded` / `application`). When the
    answer came from the application, `application_reply` holds its whole
    reply, and a check whose assignment set an `answer_path` read that part
    of it instead; each result records the `answer_path` it read (null: the
    default answer). All three are null until a terminal status and for
    `NotRan`.

    Also returns the snapshotted test set entry the run executed against —
    `test_case_id` (the live test it was originally snapshotted from),
    `name`, `input`, `expected_output`, `model_output`, `test_type_assignments`,
    and `test_case_snapshot_at` — frozen at the moment the entry was added
    to its test set, and never updated by later edits to the live test.
    This stays reachable even if the entry has since been unlinked from
    its test set (`PATCH /test-sets/{test_set_id}/entries`), or that test
    set has since been unlinked from this plan (`DELETE
    /test-plans/{test_plan_id}/entries` — test-plan-to-set links never
    freeze) — the five guards above already establish that this run
    belongs to this test plan's history, independent of either.

    Also returns `test_plan_id` (the validated path parameter) and
    `test_set_id` — the entry's *current* test set, nullable, and `null`
    once the entry has since been unlinked from it. Unlike the equivalent
    endpoint for test sets, there is no `test_set_id` in this path — a
    test plan execution spans every test set linked to the plan, so
    there's no single "the" set to scope the URL to.
    """
    return await get_run_details_by_test_plan_execution_and_run_id(
        test_plan_id, test_plan_execution_id, test_run_id, session
    )
