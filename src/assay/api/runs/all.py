from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import PaginatedExecutionMetadata, PaginatedRunMetadata
from assay.services import get_execution_metadata_all_executions, get_run_metadata_all_runs

router = APIRouter(tags=["run (all)"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    path="/runs",
    summary="List every run in the system, regardless of origin",
    responses={
        200: {
            "description": (
                "A paginated list of every run ever created (standalone, "
                "test-set-triggered, and test-plan-triggered alike) newest "
                "first."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "total": 3,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                                "status": "Pending",
                                "created_at": "2026-07-15T09:12:47.884213",
                                "origin": "Standalone",
                                "test_case_id": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                },
                                "test_set_entry_id": None,
                                "test_set_execution_id": None,
                                "test_plan_execution_id": None,
                            },
                            {
                                "id": "d4e5f6a7-b8c9-0123-def4-56789012345a",
                                "status": "Completed",
                                "created_at": "2026-07-14T18:03:21.123456",
                                "origin": "TestSet",
                                "test_case_id": None,
                                "test_set_entry_id": {
                                    "id": "e5f6a7b8-c901-2345-def6-789012345bcd"
                                },
                                "test_set_execution_id": {
                                    "id": "f6a7b8c9-0123-def4-5678-9012345abcde"
                                },
                                "test_plan_execution_id": None,
                            },
                            {
                                "id": "a7b8c9d0-1234-5abc-def6-789012345bcd",
                                "status": "Failed",
                                "created_at": "2026-07-13T11:47:02.556213",
                                "origin": "TestPlan",
                                "test_case_id": None,
                                "test_set_entry_id": {
                                    "id": "b8c9d0e1-2345-6abc-def7-89012345cdef"
                                },
                                "test_set_execution_id": None,
                                "test_plan_execution_id": {
                                    "id": "c9d0e1f2-3456-7abc-def8-9012345defab"
                                },
                            },
                        ],
                    }
                }
            },
        },
    },
    response_model=PaginatedRunMetadata,
)
async def get_all_run_metadata(
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(default=100, description="Maximum number of records to "
                                                    "return for pagination.")
) -> PaginatedRunMetadata: # pragma: no cover
    """List every run ever created, across every test, test set, and test
    plan, newest first.

    Unlike every other run-listing endpoint, this one isn't scoped to a
    single origin: a standalone run, a run produced by a test set execution,
    and a run produced by a test plan execution all show up here side by
    side, in one `created_at`-descending feed. This is what makes it useful
    for a global view (e.g. "what's running right now" across the whole
    system) without looping over every test, test set, and test plan
    individually.

    No guards run before the list is fetched — there's no parent resource
    whose ID could be wrong.

    Each item carries `origin` (`Standalone`, `TestSet`, or `TestPlan`)
    alongside `id`, `status`, and `created_at`. Only the ID field(s) that
    origin implies are non-null — `test_case_id` for `Standalone`;
    `test_set_entry_id` and `test_set_execution_id` for `TestSet`;
    `test_set_entry_id` and `test_plan_execution_id` for `TestPlan` — the
    rest are always null on that item. Results are ordered by `created_at`
    descending (ties broken by `id` descending), plus the usual `total`,
    `offset`, and `limit`.
    """
    return await get_run_metadata_all_runs(session, offset, limit)


@router.get(
    path="/runs/executions",
    summary=(
        "List every test set and test plan execution in the system "
        "(standalone runs excluded — they have no execution wrapper)"
    ),
    responses={
        200: {
            "description": (
                "A paginated list of every test set and test plan execution "
                "ever triggered (live or replayed), newest first. Does NOT "
                "include standalone runs — a standalone run has no "
                "execution wrapper to aggregate, so it never appears here "
                "regardless of status; see `GET /runs` for a listing that "
                "does include standalone runs."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "d4e5f6a7-b8c9-0123-def4-56789012345a",
                                "created_at": "2026-07-15T09:12:47.884213",
                                "origin": "TestSet",
                                "run_count": 12,
                                "test_set_id": {
                                    "id": "e5f6a7b8-c901-2345-def6-789012345bcd"
                                },
                                "test_plan_id": None,
                                "replayed_test_set_execution_id": None,
                                "replayed_test_plan_execution_id": None,
                            },
                            {
                                "id": "a7b8c9d0-1234-5abc-def6-789012345bcd",
                                "created_at": "2026-07-14T18:03:21.123456",
                                "origin": "TestPlan",
                                "run_count": 34,
                                "test_set_id": None,
                                "test_plan_id": {
                                    "id": "c9d0e1f2-3456-7abc-def8-9012345defab"
                                },
                                "replayed_test_set_execution_id": None,
                                "replayed_test_plan_execution_id": {
                                    "id": "b8c9d0e1-2345-6abc-def7-89012345cdef"
                                },
                            },
                        ],
                    }
                }
            },
        },
    },
    response_model=PaginatedExecutionMetadata,
)
async def get_all_execution_metadata(
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(default=100, description="Maximum number of records to "
                                                    "return for pagination.")
) -> PaginatedExecutionMetadata: # pragma: no cover
    """List every execution ever triggered, across every test set and test
    plan, newest first. Does NOT include standalone runs — see below.

    An execution is a `TestSetExecutionModel` or `TestPlanExecutionModel`
    row — the grouping/trigger-event record a batch of runs was fanned out
    under (`run_count`, `replayed_*_execution_id`) — never an individual
    run, and never a standalone run, which has no such wrapper at all and
    so can never appear in this listing, regardless of status (see `GET
    /runs`, which covers runs including standalone ones, and `GET
    /runs/test-sets/{test_set_id}/executions` /
    `GET /runs/test-plans/{test_plan_id}/executions`, which cover a single
    set's or plan's own execution history).

    This is the aggregate counterpart to those two scoped listing
    endpoints: instead of one call per test set or test plan, a caller gets
    every execution across the whole system in one paginated,
    `created_at`-descending feed — what a "recent executions" overview
    actually wants, without fanning out one request per test set/test plan
    in the catalog.

    No guards run before the list is fetched — there's no parent resource
    whose ID could be wrong.

    Each item carries `origin` (`TestSet` or `TestPlan`) alongside `id`,
    `created_at`, and `run_count`. Only the ID field(s) that origin implies
    are non-null — `test_set_id` (plus `replayed_test_set_execution_id` if
    it was a replay) for `TestSet`; `test_plan_id` (plus
    `replayed_test_plan_execution_id` if it was a replay) for `TestPlan` —
    the rest are always null on that item. Results are ordered by
    `created_at` descending (ties broken by `id` descending), plus the
    usual `total`, `offset`, and `limit`.
    """
    return await get_execution_metadata_all_executions(session, offset, limit)
