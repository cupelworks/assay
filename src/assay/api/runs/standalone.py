import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import (
    PaginatedStandaloneRunCreationMetadata,
    StandaloneRunCreationMetadata,
    StandaloneRunDetails,
)
from assay.services import (
    create_new_standalone_run,
    get_run_details_by_test_and_run_id,
    get_standalone_run_metadata_all_test_runs,
)

router = APIRouter(tags=["run (standalone)"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    path="/runs/standalone/{test_id}",
    responses={
        201: {
            "description": (
                "A pending run was created for the test and dispatched for "
                "execution. The run keeps its own copy of the test as it is "
                "right now (see the run's detail), so editing the test later "
                "never changes what this run was judged against. This "
                "endpoint does not call the model, score "
                "anything, or write back results itself — `status` is always "
                "`Pending` in the response, since the worker that does all of "
                "that runs separately, after this response is returned. "
                "Dispatch is best-effort: if it fails (e.g. the broker is "
                "unreachable), the run simply stays `Pending`. Once picked up, "
                "the worker promotes it to `Running`, then a terminal outcome "
                "(`Green`, `Amber`, `Red`, or `NotRan`)."
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

    A standalone run evaluates a single test directly, outside any test set
    or test plan — the quick, ad-hoc way to run a test during authoring,
    without first adding it to a set. The run keeps its own frozen copy of
    the test, taken right here at creation: its name, input, outputs and
    assigned test types with their config. That copy is what gets evaluated
    and what the run's detail shows, so editing the test afterwards never
    changes what this run was judged against. Unlike runs created via a test
    set or test plan, a standalone run has no "live vs. replay" concept —
    each new standalone run copies the test as it is at that moment.

    Two guards run before the run is created:
    - The test must exist (404).
    - The test must have at least one test type assigned (409) — otherwise
      the run would have nothing to be scored against, ever.

    This endpoint creates the run record, with its frozen copy, and
    dispatches it for execution — it does not execute anything itself; that
    happens in the worker process, once it picks up the dispatched task.
    Exactly one run is created regardless of how many test types are
    assigned to the test.

    Returns the new run's ID, status (always `Pending` at creation), creation
    timestamp, and the ID of the test it was created for.
    """
    return await create_new_standalone_run(test_id, session)


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
                                "status": "Green",
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
                "Full details for the standalone run: its post-execution "
                "results, and the frozen copy of the test it was created "
                "from (`name`, `input`, `expected_output`, `model_output`, "
                "`test_type_assignments`, `test_case_snapshot_at`) — the "
                "test exactly as it was when the run was created, so later "
                "edits to the live test never change what this shows. "
                "`results`, `error`, and `executed_at` are null until the "
                "run reaches a terminal status (`Green`, `Amber`, `Red`, or "
                "`NotRan`) — this example shows a run where every assigned "
                "test type passed."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                        "status": "Green",
                        "created_at": "2026-07-14T18:03:21.123456",
                        "test_case_id": {
                            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
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
                        "executed_at": "2026-07-14T18:03:24.981022",
                        "name": "greets the user by name",
                        "input": "Say hello to Alice.",
                        "expected_output": "Hello, Alice!",
                        "model_output": "Hello, Alice!",
                        "test_type_assignments": [
                            {"name": "Exact Match", "config": None},
                            {"name": "BLEU", "config": {"threshold": "0.4"}},
                        ],
                        "test_case_snapshot_at": {
                            "snapshot_at": "2026-07-14T18:03:21.123456"
                        },
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
    plus `results`, `error`, and `executed_at` — the latter three are null
    until the run reaches a terminal status (`Green`, `Amber`, `Red`, or
    `NotRan`), and `results`/`error` are mutually exclusive even then:
    `error` is only ever set for `NotRan`, `results` for the other three.

    Also returns the frozen copy of the test taken when the run was created
    — `name`, `input`, `expected_output`, `model_output`,
    `test_type_assignments` (each with its config) and
    `test_case_snapshot_at` — in the same shape a test-set or test-plan
    run's detail returns its entry. It's what the run is evaluated against,
    and editing the live test afterwards never changes it; `test_case_id`
    still points at the live test.
    """
    return await get_run_details_by_test_and_run_id(test_id, test_run_id, session)
