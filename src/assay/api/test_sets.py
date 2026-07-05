import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import (
    PaginatedTestSetEntriesDetails,
    PaginatedTestSetMetadataResponse,
    TestCaseID,
    TestSetCreationResponse,
    TestSetEntryID,
    TestSetMetadata,
    TestSetName,
)
from assay.services import (
    add_tests_to_test_set_by_test_id,
    create_new_test_set,
    get_all_test_sets_metadata,
    get_test_set_metadata_by_id,
    get_test_sets_linked_tests,
)

router = APIRouter(tags=["test set"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    path="/test-sets/{test_set_id}/entries",
    responses={
        200: {
            "description": "A paginated list of the test set's entries, ordered by "
                            "`name` (with `id` as a tiebreaker for entries sharing a name).",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                                "test_case_id": {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                },
                                "name": "Refund request - happy path",
                                "input": "I'd like a refund for order #4471.",
                                "expected_output": "Sure, I've processed a refund for "
                                                    "order #4471.",
                                "model_output": "Your refund for order #4471 has been "
                                                 "issued.",
                                "test_type_names": ["semantic_similarity", "toxicity"],
                            },
                            {
                                "id": "d4e5f6a7-b8c9-0123-defa-234567890123",
                                "test_case_id": {
                                    "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901"
                                },
                                "name": "Refund request - missing order id",
                                "input": "I want a refund but I don't have my order number.",
                                "expected_output": "Could you share your order number or "
                                                    "the email used at checkout?",
                                "model_output": "I'm sorry, I can't process refunds "
                                                 "without an order number.",
                                "test_type_names": ["semantic_similarity"],
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
                        "detail": "Test set with ID 'a1b2c3d4-e5f6-7890-abcd-ef1234567890' "
                                  "not found"
                    },
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=PaginatedTestSetEntriesDetails,
)
async def get_all_test_set_entries(
        test_set_id: uuid.UUID,
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip for pagination."),
        limit: int = Query(
            default=100, description="Maximum number of records to return for pagination."
        ),
) -> PaginatedTestSetEntriesDetails: # pragma: no cover
    """List all entries (snapshotted tests) belonging to a test set, paginated.

    Each entry is an immutable snapshot captured at the moment a test was added to
    the set via `POST /test-sets/{test_set_id}/entries` — `input`, `expected_output`,
    `model_output`, and `test_type_names` reflect the test's state at that time, not
    its current live state. `test_case_id` traces the entry back to the live test it
    was created from.

    Results are ordered by `name`, with `id` as a tiebreaker, so pagination is stable
    across pages even when multiple entries share the same name.

    Use `offset` and `limit` to page through results. The response includes `total`
    so the client can calculate the number of pages.
    """
    return await get_test_sets_linked_tests(test_set_id, session, offset, limit)


@router.post(
    path="/test-sets/{test_set_id}/entries",
    responses={
        200: {
            "description": "Snapshot entries created. Returns one entry ID per test added.",
            "content": {
                "application/json": {
                    "example": [
                        {"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"},
                        {"id": "b2c3d4e5-f6a7-8901-bcde-f12345678901"},
                    ]
                }
            },
        },
        404: {
            "description": (
                "The test set does not exist, or one or more test IDs in the request body "
                "were not found. No entries are created."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "test_set_not_found": {
                            "summary": "Test set not found",
                            "value": {
                                "detail": "Test set with ID '<test_set_id>' not found"
                            },
                        },
                        "tests_not_found": {
                            "summary": "One or more test IDs not found",
                            "value": {
                                "detail": "Tests with ids ['<id1>', '<id2>'] not found"
                            },
                        },
                    }
                }
            },
        },
        409: {
            "description": (
                "One or more tests are already present in the test set. "
                "No entries are created."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "Tests with ID ['<id1>'] already linked to test set"
                            " with ID '<test_set_id>'"
                        )
                    }
                }
            },
        },
    },
    response_model=list[TestSetEntryID]
)
async def add_tests_to_test_set(
        test_set_id: uuid.UUID,
        request: list[TestCaseID],
        session: SessionDep,
) -> list[TestSetEntryID]: # pragma: no cover
    """Snapshot one or more tests into a test set, freezing their current state.

    Each test in the request body is copied into an immutable entry that captures
    `name`, `input`, `expected_output`, `model_output`, and `test_type_names` at
    the moment this endpoint is called. Subsequent edits to the originating test
    have no effect on the snapshot.

    Three checks run before any data is written:
    - The test set must exist (404 if not).
    - All test IDs in the request body must exist (404 if any are missing).
    - None of the tests may already be snapshotted in this set (409 if any overlap).

    Duplicate IDs in the request body are silently deduplicated — each test produces
    exactly one entry regardless of repetition.
    """
    return await add_tests_to_test_set_by_test_id(test_set_id, request, session)


@router.get(
    path="/test-sets/{test_set_id}",
    responses={
        200: {
            "description": "Metadata for the requested test set.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "Regression suite",
                        "created_at": "2026-07-03T15:43:09.032480",
                    }
                }
            },
        },
        404: {
            "description": "No test set exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {"detail": "Test set with id <example-id> not found"},
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    },
                }
            },
        },
    },
    response_model=TestSetMetadata,
)
async def get_single_test_set_metadata(
        test_set_id: uuid.UUID,
        session: SessionDep,
) -> TestSetMetadata: # pragma: no cover
    """Retrieve metadata for a single test set by its ID.

    Returns the test set's `id`, `name`, and `created_at` timestamp.
    """
    return await get_test_set_metadata_by_id(test_set_id, session)


@router.get(
    path="/test-sets",
    responses={
        200: {
            "description": "A paginated list of test set metadata.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "offset": 0,
                        "limit": 100,
                        "items": [
                            {
                                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                "name": "Regression suite",
                                "created_at": "2026-07-03T15:43:09.032480",
                            },
                            {
                                "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                                "name": "Smoke tests",
                                "created_at": "2026-07-03T16:00:00.000000",
                            },
                        ],
                    }
                }
            },
        },
    },
    response_model=PaginatedTestSetMetadataResponse
)
async def get_test_sets_metadata(
    session: SessionDep,
    offset: int = Query(default=0, description="Number of records to skip for pagination."),
    limit: int = Query(
        default=100, description="Maximum number of records to return for pagination."
    ),
) -> PaginatedTestSetMetadataResponse: # pragma: no cover
    """List all test sets with their metadata, paginated.

    Returns each test set's `id`, `name`, and `created_at` timestamp.
    Use `offset` and `limit` to page through results. The response includes `total`
    so the client can calculate the number of pages.
    """
    return await get_all_test_sets_metadata(
        session, offset, limit
    )


@router.post(
    path="/test-sets",
    responses={
        200: {
            "description": "Test set created successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "My test set",
                    }
                }
            },
        },
        409: {
            "description": "A test set with the given name already exists.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test set with name 'My test set' already exists"
                    }
                }
            },
        },
    },
    response_model=TestSetCreationResponse,
)
async def create_test_set(
        request: TestSetName,
        session: SessionDep
) -> TestSetCreationResponse: # pragma: no cover
    """Create a new test set.

    Test set names must be unique — a 409 is returned if the name is already taken.

    Returns the created test set with its generated ID and name.
    """
    return await create_new_test_set(request, session)
