import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.api._filters import CreatedFrom, CreatedTo, Limit, Offset, search
from assay.api.runs._batch_filter import BatchFilter
from assay.db import get_session
from assay.models import TestStatus
from assay.schemas import (
    PaginatedExecutionMetadata,
    PaginatedRunMetadata,
    RunFacets,
    RunOrigin,
    RunSort,
)
from assay.services import (
    RunFilters,
    get_execution_metadata_all_executions,
    get_run_facets,
    get_run_metadata_all_runs,
)

router = APIRouter(tags=["run (all)"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


_RUN_COUNTS = {"Pending": 0, "Running": 0, "Green": 10, "Amber": 1, "Red": 1, "NotRan": 0}


def run_filters(
        status: Annotated[list[TestStatus] | None, Query(
            description="Only runs at these statuses; give it once per status.")] = None,
        origin: Annotated[list[RunOrigin] | None, Query(
            description="Only runs created this way (`Standalone`, `TestSet`, `TestPlan`); "
                        "give it once per origin.")] = None,
        batch: BatchFilter = None,
        test_set_id: Annotated[list[uuid.UUID] | None, Query(
            description="Only runs of these test sets' executions.")] = None,
        test_plan_id: Annotated[list[uuid.UUID] | None, Query(
            description="Only runs of these test plans' executions.")] = None,
        check_type: Annotated[list[str] | None, Query(
            description="Only runs asking a check of one of these types (catalogue names, "
                        "e.g. `Toxicity`); a check a statistical batch left out isn't asked.")
        ] = None,
        created_from: CreatedFrom = None,
        created_to: CreatedTo = None,
        q: Annotated[list[str] | None, search("the run's test name and its set's or plan's "
                                               "name")] = None,
) -> RunFilters:
    """The runs list's filters: a filter given several times means any of its
    values, and different filters narrow together."""
    return RunFilters(status=status, origin=origin, batch=batch, test_set_id=test_set_id,
                      test_plan_id=test_plan_id, check_type=check_type,
                      created_from=created_from, created_to=created_to, q=q)


RunFiltersDep = Annotated[RunFilters, Depends(run_filters)]


@router.get(
    path="/runs",
    summary="List every run in the system, regardless of origin",
    responses={
        200: {
            "description": (
                "A paginated list of the runs within the filters (standalone, "
                "test-set-triggered, and test-plan-triggered alike), sorted."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                                "status": "Amber",
                                "created_at": "2026-07-15T09:12:47.884213Z",
                                "batch_id": None,
                                "batch_index": None,
                                "origin": "Standalone",
                                "test_name": "Reset a password",
                                "scope_name": None,
                                "checks": {"met": 1, "total": 2, "not_met": ["Regex Match"]},
                                "error": None,
                                "test_case_id": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                },
                                "test_set_entry_id": None,
                                "test_set_execution_id": None,
                                "test_plan_execution_id": None,
                                "test_set_id": None,
                                "test_plan_id": None,
                            },
                            {
                                "id": "a7b8c9d0-1234-5abc-def6-789012345bcd",
                                "status": "NotRan",
                                "created_at": "2026-07-13T11:47:02.556213Z",
                                "batch_id": None,
                                "batch_index": None,
                                "origin": "TestPlan",
                                "test_name": "Refund window",
                                "scope_name": "Release check",
                                "checks": None,
                                "error": "The application under test didn't answer within 60 s",
                                "test_case_id": None,
                                "test_set_entry_id": {
                                    "id": "b8c9d0e1-2345-6abc-def7-89012345cdef"
                                },
                                "test_set_execution_id": None,
                                "test_plan_execution_id": {
                                    "id": "c9d0e1f2-3456-7abc-def8-9012345defab"
                                },
                                "test_set_id": None,
                                "test_plan_id": {
                                    "id": "22222222-2222-2222-2222-222222222222"
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
        filters: RunFiltersDep,
        sort: Annotated[RunSort, Query(description="`newest` (the default) or `oldest` "
                                                   "first, by creation.")] = RunSort.newest,
        offset: Offset = 0,
        limit: Limit = 100,
) -> PaginatedRunMetadata:  # pragma: no cover
    """Every run ever created, across every test, test set and test plan, within
    the filters, sorted and paged.

    A standalone run, a run of a test set execution and a run of a test plan
    execution all show up side by side, so a global view ("what's running
    right now") needs one call. No guards run first: there's no parent resource
    whose id could be wrong.

    Each item says how it was created (`origin`) with only that origin's ids
    set, and what a list shows without opening the run: `test_name` (as frozen
    when the run was created), `scope_name` (its set's or plan's), `checks`
    (met, total and the labels not met, once it ran) and `error` (for
    `NotRan`). `test_set_id`/`test_plan_id` are resolved server-side so a row
    can link to its set or plan without another lookup.

    Filters: a filter given several times means any of its values
    (`?status=Red&status=NotRan`), and different filters narrow together; `q`
    searches the test's and the set's or plan's names. `total` counts every run
    within the filters. `GET /runs/facets` counts the same runs by status and
    by origin.
    """
    return await get_run_metadata_all_runs(session, filters, sort, offset, limit)


@router.get(
    path="/runs/facets",
    summary="Count the runs within the filters by status and by origin",
    responses={200: {"content": {"application/json": {"example": {
        "status": {"Pending": 0, "Running": 2, "Green": 355, "Amber": 87, "Red": 24,
                   "NotRan": 19},
        "origin": {"Standalone": 120, "TestSet": 300, "TestPlan": 67},
    }}}}},
    response_model=RunFacets,
)
async def get_run_facets_endpoint(session: SessionDep,
                                  filters: RunFiltersDep) -> RunFacets:  # pragma: no cover
    """The runs `GET /runs` would list with the same filters, counted by status
    and by origin, in one call. Each is counted within every other filter
    chosen, so picking `status=Red` still counts every status (each within the
    other filters), and every value is present, 0 when none."""
    return await get_run_facets(session, filters)


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
                                "created_at": "2026-07-15T09:12:47.884213Z",
                                "origin": "TestSet",
                                "name": "Support answers",
                                "run_count": 12,
                                "runs": {**_RUN_COUNTS, "Green": 10, "Amber": 1, "Red": 1},
                                "test_set_id": {
                                    "id": "e5f6a7b8-c901-2345-def6-789012345bcd"
                                },
                                "test_plan_id": None,
                                "replayed_test_set_execution_id": None,
                                "replayed_test_plan_execution_id": None,
                            },
                            {
                                "id": "a7b8c9d0-1234-5abc-def6-789012345bcd",
                                "created_at": "2026-07-14T18:03:21.123456Z",
                                "origin": "TestPlan",
                                "name": "Release check",
                                "run_count": 34,
                                "runs": {**_RUN_COUNTS, "Green": 30, "Amber": 2, "Red": 0,
                                         "NotRan": 2},
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
                                                    "return for pagination."),
        batch: BatchFilter = None,
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
    `created_at`, its set's or plan's `name`, `run_count` and its runs by status
    (`runs`). Only the ID field(s) that origin implies
    are non-null — `test_set_id` (plus `replayed_test_set_execution_id` if
    it was a replay) for `TestSet`; `test_plan_id` (plus
    `replayed_test_plan_execution_id` if it was a replay) for `TestPlan` —
    the rest are always null on that item. Results are ordered by
    `created_at` descending (ties broken by `id` descending), plus the
    usual `total`, `offset`, and `limit`.
    """
    return await get_execution_metadata_all_executions(session, offset, limit, batch)
