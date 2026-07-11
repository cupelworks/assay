import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import (
    ModifyTestPlanRequest,
    PaginatedTestPlanEntriesDetails,
    PaginatedTestPlanMetadataResponse,
    TestPlanCreationResponse,
    TestPlanEntryID,
    TestPlanMetadata,
    TestPlanName,
    TestSetID,
)
from assay.services import (
    add_test_sets_to_test_plan_by_id,
    create_new_test_plan,
    get_all_test_plan_entries_metadata,
    get_all_test_plans_metadata,
    get_test_plan_metadata_by_id,
    update_test_plan_by_id,
)

router = APIRouter(tags=["test plan"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    path="/test-plans/{test_plan_id}/entries",
    responses={
        200: {
            "description": "Test sets linked. Returns one entry ID per test set added.",
            "content": {
                "application/json": {
                    "example": [
                        {"id": "c3d4e5f6-a7b8-9012-cdef-123456789012"},
                        {"id": "d4e5f6a7-b8c9-0123-defa-234567890123"},
                    ]
                }
            },
        },
        404: {
            "description": (
                "The test plan does not exist, or one or more test set IDs in "
                "the request body were not found. No entries are created."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "test_plan_not_found": {
                            "summary": "Test plan not found",
                            "value": {
                                "detail": "Test plan with ID '<test_plan_id>' not found"
                            },
                        },
                        "test_sets_not_found": {
                            "summary": "One or more test set IDs not found",
                            "value": {
                                "detail": "Test sets with IDs ['<id1>', '<id2>'] not found"
                            },
                        },
                    }
                }
            },
        },
        409: {
            "description": (
                "One or more test sets are already linked to this test plan. "
                "No entries are created."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "Test sets with ID ['<id1>'] already linked to test plan"
                            " with ID '<test_plan_id>'"
                        )
                    }
                }
            },
        },
    },
    response_model=list[TestPlanEntryID],
)
async def add_test_sets_to_a_test_plan(
        test_plan_id: uuid.UUID,
        request: list[TestSetID],
        session: SessionDep,
) -> list[TestPlanEntryID]: # pragma: no cover
    """Link one or more test sets to a test plan.

    Each test set in the request body becomes an entry linking it to this plan.
    The link only stores the test set's ID — it is not a snapshot, so any later
    changes to the test set (renaming it, adding or removing its entries) are
    reflected automatically whenever the plan's test sets are listed via
    `GET /test-plans/{test_plan_id}/entries`.

    Three checks run before any data is written:
    - The test plan must exist (404 if not).
    - All test set IDs in the request body must exist (404 if any are missing).
    - None of the test sets may already be linked to this plan (409 if any overlap).

    Duplicate IDs in the request body are silently deduplicated — each test set
    produces exactly one entry regardless of repetition.
    """
    return await add_test_sets_to_test_plan_by_id(
        test_plan_id, request, session
    )


@router.patch(
    path="/test-plans/{test_plan_id}",
    responses={
        200: {
            "description": "Test plan renamed successfully. Returns the full "
                            "updated test plan.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "Renamed regression plan",
                        "created_at": "2026-07-03T15:43:09.032480",
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
        409: {
            "description": "Another test plan already has the requested name. "
                            "Re-submitting the plan's own current, unchanged name "
                            "is not an error and does not trigger this check.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test plan with name 'My test plan' already exists"
                    }
                }
            },
        },
        422: {
            "description": "Validation error — an unknown field was sent in the "
                            "request body, or `name` was omitted.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "type": "extra_forbidden",
                                "loc": ["body", "id"],
                                "msg": "Extra inputs are not permitted",
                            }
                        ]
                    }
                }
            },
        },
    },
    response_model=TestPlanMetadata,
)
async def update_a_test_plan_metadata(
        test_plan_id: uuid.UUID,
        request: ModifyTestPlanRequest,
        session: SessionDep,
) -> TestPlanMetadata: # pragma: no cover
    """Rename a test plan by ID.

    `name` is required in the body, but accepts `null` to explicitly leave the
    name unchanged — this endpoint currently only supports renaming, so a `null`
    name is a no-op that still returns the plan's current metadata. Unknown body
    fields are rejected with a 422 — the schema uses `extra="forbid"` to prevent
    silently ignoring misplaced fields such as `id`.

    Test plan names must be unique. Submitting the plan's own current name is
    always allowed (it's treated as no change); submitting a name already used by
    a *different* test plan returns a 409.

    Returns the full updated test plan so the client does not need a follow-up
    GET to refresh.

    Returns a 404 if no test plan with the given ID exists.
    """
    return await update_test_plan_by_id(test_plan_id, request, session)


@router.get(
    path="/test-plans/{test_plan_id}/entries",
    responses={
        200: {
            "description": "A paginated list of the test sets included in the test "
                            "plan, ordered by the test set's `name` (with its `id` "
                            "as a tiebreaker for test sets sharing a name).",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                                "test_set": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                    "name": "Regression suite",
                                    "created_at": "2026-07-03T15:43:09.032480",
                                },
                            },
                            {
                                "id": "d4e5f6a7-b8c9-0123-defa-234567890123",
                                "test_set": {
                                    "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                                    "name": "Smoke tests",
                                    "created_at": "2026-07-03T16:00:00.000000",
                                },
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
    response_model=PaginatedTestPlanEntriesDetails,
)
async def get_all_test_sets_metadata_in_a_test_plan(
        test_plan_id: uuid.UUID,
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(default=100, description="Maximum number of records to "
                                                    "return for pagination."),
) -> PaginatedTestPlanEntriesDetails: # pragma: no cover
    """List the test sets included in a test plan, paginated.

    Each item is an entry linking the test plan to one of its test sets: `id` is
    the entry's own identifier (the plan-to-set link, not the test set's), and
    `test_set` embeds that test set's `id`, `name`, and `created_at`. Use the
    embedded `test_set.id` to fetch the snapshotted tests inside that set via
    `GET /test-sets/{test_set_id}/entries`.

    Results are ordered by the linked test set's `name`, with its `id` as a
    tiebreaker, so pagination is stable across pages even when multiple test sets
    share the same name.

    Use `offset` and `limit` to page through results. The response includes `total`
    so the client can calculate the number of pages.
    """
    return await get_all_test_plan_entries_metadata(test_plan_id, session, offset, limit)


@router.get(
    path="/test-plans/{test_plan_id}",
    responses={
        200: {
            "description": "Metadata for the requested test plan.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "Regression plan",
                        "created_at": "2026-07-03T15:43:09.032480",
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
    response_model=TestPlanMetadata,
)
async def get_single_test_plan_metadata(
        test_plan_id: uuid.UUID,
        session: SessionDep,
) -> TestPlanMetadata: # pragma: no cover
    """Retrieve metadata for a single test plan.

    Returns the test plan's `id`, `name`, and `created_at` timestamp.
    """
    return await get_test_plan_metadata_by_id(test_plan_id, session)


@router.get(
    path="/test-plans",
    responses={
        200: {
            "description": "A paginated list of test plan metadata.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                "name": "Regression plan",
                                "created_at": "2026-07-03T15:43:09.032480",
                            },
                            {
                                "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                                "name": "Smoke plan",
                                "created_at": "2026-07-03T16:00:00.000000",
                            },
                        ],
                    }
                }
            },
        },
    },
    response_model=PaginatedTestPlanMetadataResponse,
)
async def get_test_plans_metadata(
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(default=100, description="Maximum number of records to "
                                                    "return for pagination.")
) -> PaginatedTestPlanMetadataResponse: # pragma: no cover
    """List all test plans with their metadata, paginated.

    Returns each test plan's `id`, `name`, and `created_at` timestamp.
    Use `offset` and `limit` to page through results. The response includes `total`
    so the client can calculate the number of pages.
    """
    return await get_all_test_plans_metadata(session, offset, limit)


@router.post(
    path="/test-plans",
    responses={
        200: {
            "description": "Test plan created successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "My test plan",
                    }
                }
            },
        },
        409: {
            "description": "A test plan with the given name already exists.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test plan with name 'My test plan' already exists"
                    }
                }
            },
        },
    },
    response_model=TestPlanCreationResponse,
)
async def create_test_plan(
        request: TestPlanName,
        session: SessionDep,
) -> TestPlanCreationResponse: # pragma: no cover
    """Create a new test plan.

    Test plan names must be unique — a 409 is returned if the name is already taken.

    Returns the created test plan with its generated ID and name.
    """
    return await create_new_test_plan(request, session)
