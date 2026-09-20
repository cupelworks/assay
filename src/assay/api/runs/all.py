from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import PaginatedRunMetadata
from assay.services import get_run_metadata_all_runs

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
