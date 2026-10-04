import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.api._filters import (
    CreatedEdges,
    CreatedFrom,
    CreatedTo,
    Ids,
    Limit,
    Membership,
    Offset,
    Runs,
    Verdicts,
    search,
)
from assay.api._standing_examples import SCOPE_NEVER_RAN, SCOPE_RAN
from assay.db import get_session
from assay.schemas import (
    ModifyTestCaseRequest,
    ModifyTestSetMetadataRequest,
    PaginatedTestPlanLinks,
    PaginatedTestSetEntriesDetails,
    PaginatedTestSetMetadataResponse,
    ScopeSort,
    TestCaseID,
    TestSetCreationResponse,
    TestSetEntryDetails,
    TestSetEntryID,
    TestSetFacets,
    TestSetMetadata,
    TestSetName,
)
from assay.services import (
    add_tests_to_test_set_by_test_id,
    create_new_test_set,
    delete_test_set_by_id,
    delete_test_set_entries_by_id,
    get_all_test_sets_metadata,
    get_test_plans_linking_set,
    get_test_set_linked_test_by_entry_id,
    get_test_set_metadata_by_id,
    get_test_sets_linked_tests,
    modify_entry_by_id,
    unlink_test_set_entries_by_id,
    update_test_set_metadata_by_id,
)
from assay.services.test_sets.get_test_sets_metadata import TestSetFilters, get_test_set_facets

router = APIRouter(tags=["test set / test set entry"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def test_set_filters(
        latest_verdict: Verdicts = None,
        latest_run: Runs = None,
        in_test_plan: Annotated[Membership, Query(
            description="`any`: sets a plan links; `none`: sets no plan links; a plan's id: "
                        "sets that plan links. Repeatable.")] = None,
        holds_test: Annotated[Ids, Query(
            description="Only sets holding a copy of one of these tests.")] = None,
        created_from: CreatedFrom = None,
        created_to: CreatedTo = None,
        q: Annotated[list[str] | None, search("the set's name")] = None,
) -> TestSetFilters:
    return TestSetFilters(latest_verdict=latest_verdict, latest_run=latest_run,
                          in_test_plan=in_test_plan, holds_test=holds_test,
                          created_from=created_from, created_to=created_to, q=q)


@router.get(
    path="/test-sets/facets",
    summary="Count the test sets within the filters by each filter's values",
    responses={200: {"content": {"application/json": {"example": {
        "latest_verdict": {"Pending": 0, "Running": 0, "Passed": 1, "Failed": 0,
                           "Inconclusive": 0, "Incomplete": 0, "NotRan": 0, "Done": 0,
                           "none": 9},
        "latest_run": {"Running": 0, "Green": 1, "Amber": 2, "Red": 1, "NotRan": 2, "never": 4},
        "in_test_plan": {"any": 6, "none": 4, "b8c9d0e1-2345-6abc-def7-89012345cdef": 3},
        "holds_test": {"a1b2c3d4-e5f6-7890-abcd-ef1234567890": 2},
        "created": None,
    }}}}},
    response_model=TestSetFacets,
)
async def get_test_sets_facets(
        session: SessionDep,
        filters: Annotated[TestSetFilters, Depends(test_set_filters)],
        created_edges: CreatedEdges = None,
) -> TestSetFacets:  # pragma: no cover
    """The test sets `GET /test-sets` would list with the same filters, counted by
    each filter's values: each facet within every other filter chosen. A set's
    `latest_run` is its newest execution's outcome: `Running` while any run is
    Pending or Running, else the first of NotRan, Red, Amber, Green."""
    return await get_test_set_facets(session, filters, created_edges)


@router.patch(
    path="/test-sets/{test_set_id}/entries",
    responses={
        200: {
            "description": (
                "All requested entries were unlinked from the test set in a single "
                "operation. The response body is an empty object. Each entry's row, "
                "its snapshot content (`input`, `expected_output`, `model_output`, "
                "etc.), and any runs recorded against it are all left untouched — "
                "only its membership in this test set is removed. The test set "
                "itself and its remaining entries are also unaffected."
            ),
            "content": {
                "application/json": {
                    "example": {}
                }
            },
        },
        404: {
            "description": (
                "No test set exists with the given ID, or one or more requested "
                "entry IDs do not resolve to an entry within that test set — "
                "either because no entry with that ID exists at all, or because "
                "it belongs to a different test set. Nothing is unlinked, even if "
                "some of the requested entries were valid."
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
                        "entries_not_found": {
                            "summary": "One or more entries not found in this test set",
                            "value": {
                                "detail": "Test entries with ID '[<entry_id>, ...]' "
                                          "not linked to test set with ID "
                                          "'<test_set_id>'"
                            },
                        },
                    }
                }
            },
        },
    },
)
async def unlink_test_set_entry_from_a_test_set(
        test_set_id: uuid.UUID,
        request: list[TestSetEntryID],
        session: SessionDep,
) -> dict: # pragma: no cover
    """Unlink one or more entries from a test set without deleting them.

    Unlike `DELETE /test-sets/{test_set_id}/entries`, this is not a destructive
    operation and carries no runs-based guard: an entry that has already been run
    can still be unlinked, because unlinking never touches the entry's frozen
    content or the runs recorded against it — it only clears the entry's
    membership in this test set. Use this endpoint when you want to remove a
    run-having entry from a set without losing its execution history; use `DELETE`
    when you want the entry itself gone, which is only permitted while it has no
    runs.

    Every requested entry must exist within this test set. If any requested entry
    is missing, or belongs to a different test set, the entire request is
    rejected (404) and **nothing is unlinked** — this is all-or-nothing, not a
    partial/best-effort operation.

    On success the response is an empty object.
    """
    await unlink_test_set_entries_by_id(test_set_id, request, session)
    return {}


@router.patch(
    path="/test-sets/{test_set_id}",
    responses={
        200: {
            "description": "Test set renamed successfully. Returns the full "
                            "updated test set.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "Renamed regression suite",
                        "created_at": "2026-07-03T15:43:09.032480Z",
                        "entry_count": 12,
                        "test_plan_count": 1,
                        **SCOPE_RAN,
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
        409: {
            "description": "Another test set already has the requested name. "
                            "Re-submitting the set's own current, unchanged name "
                            "is not an error and does not trigger this check.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test set with name 'My test set' already exists"
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
    response_model=TestSetMetadata,
)
async def update_a_test_set_metadata(
        test_set_id: uuid.UUID,
        request: ModifyTestSetMetadataRequest,
        session: SessionDep,
) -> TestSetMetadata: # pragma: no cover
    """Rename a test set by ID.

    `name` is required in the body, but accepts `null` to explicitly leave the
    name unchanged — this endpoint currently only supports renaming, so a `null`
    name is a no-op that still returns the set's current metadata. Unknown body
    fields are rejected with a 422 — the schema uses `extra="forbid"` to prevent
    silently ignoring misplaced fields such as `id`.

    Test set names must be unique. Submitting the set's own current name is
    always allowed (it's treated as no change); submitting a name already used by
    a *different* test set returns a 409.

    Returns the full updated test set so the client does not need a follow-up
    GET to refresh.

    Returns a 404 if no test set with the given ID exists.
    """
    return await update_test_set_metadata_by_id(test_set_id, request, session)


@router.delete(
    path="/test-sets/{test_set_id}/entries",
    responses={
        200: {
            "description": (
                "All requested entries were deleted in a single operation. The "
                "response body is an empty object. The test set itself, its "
                "remaining entries, and the live tests those entries were "
                "snapshotted from are all left untouched."
            ),
            "content": {
                "application/json": {
                    "example": {}
                }
            },
        },
        404: {
            "description": (
                "No test set exists with the given ID, or one or more requested "
                "entry IDs do not resolve to an entry within that test set — "
                "either because no entry with that ID exists at all, or because "
                "it belongs to a different test set. Nothing is deleted, even if "
                "some of the requested entries were valid."
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
                        "entries_not_found": {
                            "summary": "One or more entries not found in this test set",
                            "value": {
                                "detail": "Test entries with ID '[<entry_id>, ...]' "
                                          "not linked to test set with ID "
                                          "'<test_set_id>'"
                            },
                        },
                    }
                }
            },
        },
        409: {
            "description": (
                "One or more of the requested entries have already been executed "
                "at least once, i.e. they have one or more runs. Executed entries "
                "are frozen so that each run's record of what it evaluated against "
                "stays accurate, so none of the requested entries can be deleted "
                "while any of them still has a run referencing it. Nothing is "
                "deleted, even if some of the requested entries have no runs."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": "The Test Set Entries with ID '[<entry_id>, ...]' "
                                  "have runs, therefore they can't be deleted"
                    }
                }
            },
        },
    },
)
async def delete_specific_test_set_entries(
        test_set_id: uuid.UUID,
        request: list[TestSetEntryID],
        session: SessionDep,
) -> dict: # pragma: no cover
    """Delete one or more entries from a test set in a single bulk operation.

    Only the requested entries are removed — the test set itself, any of its
    entries not included in the request, and the live tests those entries
    were originally snapshotted from are all **not** affected.

    Every requested entry must exist within this test set, and none of them
    may have ever been run. If any requested entry is missing, or belongs to
    a different test set, or has at least one run, the entire request is
    rejected (404 or 409, respectively) and **nothing is deleted** — this is
    all-or-nothing, not a partial/best-effort delete.

    On success the response is an empty object.
    """
    await delete_test_set_entries_by_id(test_set_id, request, session)
    return {}


@router.delete(
    path="/test-sets/{test_set_id}",
    responses={
        200: {
            "description": (
                "The test set and every entry it contained were deleted. The "
                "response body is an empty object. The live tests those entries "
                "were snapshotted from are left untouched."
            ),
            "content": {
                "application/json": {
                    "example": {}
                }
            },
        },
        404: {
            "description": "No test set exists with the given ID. Nothing is deleted.",
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
                "At least one entry in the test set has already been executed, i.e. "
                "it has one or more runs. Executed entries are frozen so that each "
                "run's record of what it evaluated against stays accurate, so the "
                "test set cannot be deleted while any of them exist. Nothing is "
                "deleted."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test set with ID '<test_set_id>' can't be deleted "
                                  "because one or more of its entries have runs"
                    }
                }
            },
        },
    },
)
async def delete_a_test_set(
        test_set_id: uuid.UUID,
        session: SessionDep,
) -> dict: # pragma: no cover
    """Delete a test set and every entry snapshotted into it.

    Deleting a test set cascades to all of its entries in a single operation, so
    the set and its snapshots are removed together. The live tests those entries
    were originally snapshotted from are **not** affected — only the set and its
    entries are deleted.

    The delete is permitted only while none of the entries have been run. Once an
    entry has at least one run it is frozen — so that the run's record of what it
    executed against stays accurate — and the entire request is rejected with a
    409 without removing anything.

    On success the response is an empty object.
    """
    await delete_test_set_by_id(test_set_id, session)
    return {}


@router.patch(
    path="/test-sets/{test_set_id}/entries/{entry_id}",
    responses={
        200: {
            "description": "The full updated test set entry.",
            "content": {
                "application/json": {
                    "example": {
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
                        "test_type_assignments": [
                            {"name": "Cosine Similarity", "config": {"threshold": "0.75"}},
                            {
                                "name": "Toxicity",
                                "config": {"rubric": "Flag anything that could read as rude."},
                            },
                        ],
                        "has_runs": False,
                    }
                }
            },
        },
        404: {
            "description": (
                "No test set exists with the given ID, or no entry with the given "
                "ID exists within that test set."
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
                        "entry_not_found": {
                            "summary": "Entry not found in this test set",
                            "value": {
                                "detail": "Test entry with ID '<entry_id>' not found "
                                          "in test set with ID '<test_set_id>'"
                            },
                        },
                    }
                }
            },
        },
        409: {
            "description": (
                "The entry has already been executed at least once and is frozen. "
                "No fields are updated."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test entry with ID '<entry_id>' can't be modified"
                                  " because it has runs"
                    }
                }
            },
        },
        422: {
            "description": (
                "One or more test type names are not in the catalogue, an assignment "
                "is missing a required config field, a config value isn't valid for "
                "its field (a number out of range, a pattern that doesn't compile, "
                "JSON, a JSONPath or a JSON Schema that doesn't parse) or an "
                "`answer_path` doesn't parse, or a reference-required type "
                "(e.g. Exact Match, ROUGE) is left with no `expected_output` once this "
                "update is applied — considering both the request and whatever the "
                "entry already had for any field this request doesn't touch."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "unknown_test_type": {
                            "summary": "Unknown test type name",
                            "value": {"detail": "Unknown test types: ['Invalid Type']"},
                        },
                        "unparseable_value": {
                            "summary": "A config value or an answer_path that isn't valid",
                            "value": {"detail": (
                                "'ROUGE' config field 'threshold' must be between 0 and 1; "
                                "'Regex Match' config field 'pattern' is not a valid regex "
                                "pattern: unterminated character set at position 5; "
                                "'Contains' answer_path is not a valid JSONPath: Parse "
                                "error near the end of string!"
                            )},
                        },
                        "missing_expected_output": {
                            "summary": "Reference-required type with no expected_output",
                            "value": {
                                "detail": (
                                    "Test types ['Exact Match'] require a non-empty "
                                    "expected_output, but none was provided"
                                )
                            },
                        },
                    }
                }
            },
        },
    },
    response_model=TestSetEntryDetails,
)
async def update_a_test_set_entry(
        test_set_id: uuid.UUID,
        entry_id: uuid.UUID,
        request: ModifyTestCaseRequest,
        session: SessionDep,
) -> TestSetEntryDetails: # pragma: no cover
    """Partially update a test set entry, as long as it has never been executed.

    Entries are editable only until their first run — once a run references the
    entry, it is frozen and a 409 is returned, so the run's record of what it
    executed against stays accurate. Note that an edited entry no longer reflects
    the originating test's state at snapshot time; `test_case_id` keeps pointing
    at the live test regardless.

    Only fields included in the request body are written — omitted fields are left
    unchanged:

    | Field | Sent with a value | Sent as `null` | Left out |
    |---|---|---|---|
    | `expected_output`, `model_output` | set | **cleared** | unchanged |
    | `name`, `input` | set | unchanged (neither can be empty) | unchanged |
    | `test_type_assignments` | replaces the list (`[]` clears it) | unchanged | unchanged |

    Each provided name must exist in
    the test types catalogue and satisfy that type's required config fields — a 422
    is returned otherwise. Considering the effective state after this update, a type
    requiring a reference (e.g. Exact Match, ROUGE) also requires a non-empty
    `expected_output` — a 422 is returned if that's not the case, even if this
    particular request doesn't touch either field directly. An assignment's optional
    `answer_path` (the part of the application's reply that check reads) must parse,
    and every config value must be valid for its field — a number within its range, a
    pattern that compiles, JSON, a JSONPath or a JSON Schema that parses: a 422
    otherwise.

    Returns the full updated entry, so no follow-up GET is needed.
    """
    return await modify_entry_by_id(test_set_id, entry_id, request, session)


@router.get(
    path="/test-sets/{test_set_id}/entries/{entry_id}",
    responses={
        200: {
            "description": "The requested test set entry.",
            "content": {
                "application/json": {
                    "example": {
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
                        "test_type_assignments": [
                            {"name": "Cosine Similarity", "config": {"threshold": "0.75"}},
                            {
                                "name": "Toxicity",
                                "config": {"rubric": "Flag anything that could read as rude."},
                            },
                        ],
                        "has_runs": True,
                    }
                }
            },
        },
        404: {
            "description": (
                "No test set exists with the given ID, or no entry with the given "
                "ID exists within that test set."
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
                        "entry_not_found": {
                            "summary": "Entry not found in this test set",
                            "value": {
                                "detail": "Test entry with ID '<entry_id>' not found "
                                          "in test set with ID '<test_set_id>'"
                            },
                        },
                    }
                }
            },
        },
    },
    response_model=TestSetEntryDetails,
)
async def get_single_test_set_entry(
        test_set_id: uuid.UUID,
        entry_id: uuid.UUID,
        session: SessionDep,
) -> TestSetEntryDetails: # pragma: no cover
    """Retrieve a single entry from a test set by its ID.

    Returns the entry's snapshot data (`name`, `input`, `expected_output`,
    `model_output`, `test_type_assignments`) as it was at the moment the test was added to
    the set — or as it was last edited via `PATCH`, if it has been edited and has no
    runs yet — along with `test_case_id`, which traces the entry back to the live
    test it was created from.
    """
    return await get_test_set_linked_test_by_entry_id(test_set_id, entry_id, session)


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
                                "test_type_assignments": [
                                    {"name": "Cosine Similarity", "config": {"threshold": "0.75"}},
                                    {
                                        "name": "Toxicity",
                                        "config": {"rubric": "Flag anything rude."},
                                    },
                                ],
                                "has_runs": True,
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
                                "test_type_assignments": [
                                    {"name": "Cosine Similarity", "config": {"threshold": "0.75"}},
                                ],
                                "has_runs": False,
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

    Each entry is a snapshot captured at the moment a test was added to the set via
    `POST /test-sets/{test_set_id}/entries` — `input`, `expected_output`,
    `model_output`, and `test_type_assignments` reflect the test's state at that time, not
    its current live state, and never re-sync from it. The entry itself can still be
    edited directly via `PATCH /test-sets/{test_set_id}/entries/{entry_id}` until it
    has been run at least once, after which it freezes. `test_case_id` traces the
    entry back to the live test it was created from.

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
    """Snapshot one or more tests into a test set.

    Each test in the request body is copied into an entry that captures `name`,
    `input`, `expected_output`, `model_output`, and `test_type_assignments` at the
    moment this endpoint is called. Subsequent edits to the originating test have
    no effect on the entry — but the entry itself can still be edited directly via
    `PATCH /test-sets/{test_set_id}/entries/{entry_id}` until it has been run at
    least once, after which it freezes.

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
                        "created_at": "2026-07-03T15:43:09.032480Z",
                        "entry_count": 12,
                        "test_plan_count": 1,
                        **SCOPE_RAN,
                    }
                }
            },
        },
        404: {
            "description": "No test set exists with the given ID.",
            "content": {
                "application/json": {
                    "example": {"detail": "Test set with ID '<test_set_id>' not found"},
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

    Returns the test set's `id`, `name`, `created_at` timestamp, and `entry_count`
    (the number of entries currently in the set).
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
                                "created_at": "2026-07-03T15:43:09.032480Z",
                                "entry_count": 12,
                                "test_plan_count": 1,
                                **SCOPE_RAN,
                            },
                            {
                                "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                                "name": "Smoke tests",
                                "created_at": "2026-07-03T16:00:00.000000Z",
                                "entry_count": 4,
                                "test_plan_count": 0,
                                **SCOPE_NEVER_RAN,
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
    filters: Annotated[TestSetFilters, Depends(test_set_filters)],
    sort: Annotated[ScopeSort, Query(
        description="`latest_run` (the default: newest execution outside a batch first, never "
                    "run last), `created` (newest first) or `name` (ignoring case).")
    ] = ScopeSort.latest_run,
    offset: Offset = 0,
    limit: Limit = 100,
) -> PaginatedTestSetMetadataResponse:  # pragma: no cover
    """List the test sets within the filters, sorted and paged, each with its
    entries counted, the plans linking it counted, and how it stands. `total`
    counts every set within the filters."""
    return await get_all_test_sets_metadata(session, filters, sort, offset, limit)


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


@router.get(
    path="/test-sets/{test_set_id}/test-plans",
    summary="List the test plans linking a test set",
    responses={
        200: {"content": {"application/json": {"example": {
            "total": 1, "offset": 0, "limit": 100,
            "items": [{
                "test_plan": {
                    "id": "b8c9d0e1-2345-6abc-def7-89012345cdef", "name": "Release check",
                    "created_at": "2026-09-30T11:00:00Z", "linked_set_count": 3,
                    "latest_batch": None, "latest_execution": None, "execution_count": 0,
                    "has_runs": False},
                "entry_id": "c9d0e1f2-3456-7abc-def8-9012345defab",
            }],
        }}}},
        404: {"description": "No test set exists with the given ID.",
              "content": {"application/json": {"example": {
                  "detail": "Test set with ID '<test_set_id>' not found"}}}},
    },
    response_model=PaginatedTestPlanLinks,
)
async def get_test_plans_linking(
        test_set_id: uuid.UUID,
        session: SessionDep,
        offset: Offset = 0,
        limit: Limit = 100,
) -> PaginatedTestPlanLinks:  # pragma: no cover
    """The test plans linking this test set, by name, each as the plans list shows
    it (its latest execution included), with `entry_id`: the plan's entry that
    links the set, the id `DELETE /test-plans/{id}/entries` unlinks by."""
    return await get_test_plans_linking_set(test_set_id, session, offset, limit)
