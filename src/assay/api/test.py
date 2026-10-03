import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.models import TestTypes
from assay.schemas import (
    CreateTestCaseFromDatasetRequest,
    CreateTestCaseFromDatasetResponse,
    CreateTestCaseRequest,
    CreateTestCaseResponse,
    ModifyTestCaseRequest,
    PaginatedTestCases,
    TestCaseID,
    TestTypesSchema,
)
from assay.services import (
    create_new_test,
    create_new_test_from_dataset,
    delete_test_by_id,
    get_all_created_tests,
    get_test_case_by_id,
    get_test_types_by_category,
    modify_test_by_id,
)

router = APIRouter(tags=["test"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    path="/tests/types",
    summary="List available test types",
    responses={
        200: {
            "description": "Every catalogue entry in the given category, "
                           "ordered by name (descending).",
            "content": {
                "application/json": {
                    "examples": {
                        "nlp_metric": {
                            "summary": "NLP metrics: thresholds on a 0–1 and a −1 to 1 scale",
                            "value": [
                                {
                                    "id": "696bf21b-6263-4024-8182-ddaba33d5b30",
                                    "name": "ROUGE",
                                    "category": "nlp_metric",
                                    "description": "Measures the longest sequence of words "
                                                   "the output shares with the expected "
                                                   "text, in order (ROUGE-L F1).",
                                    "is_active": True,
                                    "created_at": "2026-05-27T19:36:01.272322",
                                    "best_for": "Summarization tasks.",
                                    "cost": "fast",
                                    "limitations": "Doesn't account for semantic meaning; "
                                                   "penalizes valid paraphrases.",
                                    "config_fields": [
                                        {
                                            "key": "reference",
                                            "label": "Reference text",
                                            "kind": "reference",
                                            "required": True,
                                            "min": None,
                                            "max": None,
                                            "integer": False,
                                            "placeholder": None,
                                            "hint": None,
                                        },
                                        {
                                            "key": "threshold",
                                            "label": "Minimum score to pass",
                                            "kind": "numeric",
                                            "required": True,
                                            "min": 0.0,
                                            "max": 1.0,
                                            "integer": False,
                                            "placeholder": "0.5",
                                            "hint": "Shared wording, in order. A close "
                                                    "paraphrase scores about 0.6, an "
                                                    "unrelated answer about 0.2.",
                                        },
                                    ],
                                    "engine": "rouge",
                                    "engine_settings": {
                                        "variant": "rougeL",
                                        "measure": "f1",
                                        "stemmer": True,
                                    },
                                    "comparison": "gte",
                                },
                                {
                                    "id": "ede3f4b9-1f31-4296-90ca-f34e6e3adb1f",
                                    "name": "Cosine Similarity (multilingual)",
                                    "category": "nlp_metric",
                                    "description": "Checks whether the answer means the same "
                                                   "as the expected text, as a whole, in "
                                                   "about 50 languages including German, "
                                                   "French and Italian — even when the answer "
                                                   "and the expected text are in different "
                                                   "languages.",
                                    "is_active": True,
                                    "created_at": "2026-10-01T03:00:00.000000",
                                    "best_for": "Answers in languages other than English, or "
                                                "in a different language from the expected "
                                                "text.",
                                    "cost": "fast",
                                    "limitations": "Measures closeness of meaning, not "
                                                   "correctness: an answer saying the "
                                                   "opposite about the same thing can still "
                                                   "score high. Only about the first 100 "
                                                   "words of each text count. Its model is "
                                                   "larger, so the first check on a worker "
                                                   "takes longer.",
                                    "config_fields": [
                                        {
                                            "key": "reference",
                                            "label": "Reference text",
                                            "kind": "reference",
                                            "required": True,
                                            "min": None,
                                            "max": None,
                                            "integer": False,
                                            "placeholder": None,
                                            "hint": None,
                                        },
                                        {
                                            "key": "threshold",
                                            "label": "Minimum score to pass",
                                            "kind": "numeric",
                                            "required": True,
                                            "min": -1.0,
                                            "max": 1.0,
                                            "integer": False,
                                            "placeholder": "0.7",
                                            "hint": "Closeness of meaning, in any of its "
                                                    "languages. A close paraphrase scores "
                                                    "about 0.95, an unrelated answer about 0.",
                                        },
                                    ],
                                    "engine": "embedding_cosine",
                                    "engine_settings": {
                                        "model": "paraphrase-multilingual-MiniLM-L12-v2",
                                    },
                                    "comparison": "gte",
                                },
                            ],
                        },
                        "deterministic": {
                            "summary": "Deterministic checks: json/jsonpath fields, an "
                                       "open-ended whole-number bound",
                            "value": [
                                {
                                    "id": "6417a3dd-253e-482b-af8d-21672a5e925c",
                                    "name": "Word Count Limit",
                                    "category": "deterministic",
                                    "description": "Checks that the output has at most a "
                                                   "maximum number of words, and optionally "
                                                   "at least a minimum.",
                                    "is_active": True,
                                    "created_at": "2026-09-29T21:50:26.040821",
                                    "best_for": "Length requirements on prose: summaries, "
                                                "descriptions, short answers that must "
                                                "fit a card or a screen.",
                                    "cost": "very_fast",
                                    "limitations": "Words are whitespace-separated, so a "
                                                   "hyphenated term or a number counts as "
                                                   "one; says nothing about what the words "
                                                   "say.",
                                    "config_fields": [
                                        {
                                            "key": "max",
                                            "label": "Maximum words",
                                            "kind": "numeric",
                                            "required": True,
                                            "min": 0.0,
                                            "max": None,
                                            "integer": True,
                                            "placeholder": "100",
                                            "hint": "An answer of exactly this length "
                                                    "meets it.",
                                        },
                                        {
                                            "key": "min",
                                            "label": "Minimum words",
                                            "kind": "numeric",
                                            "required": False,
                                            "min": 0.0,
                                            "max": None,
                                            "integer": True,
                                            "placeholder": None,
                                            "hint": "Optional. Leave it empty for no "
                                                    "minimum.",
                                        },
                                    ],
                                    "engine": "length",
                                    "engine_settings": {"unit": "words"},
                                    "comparison": None,
                                },
                                {
                                    "id": "f11d56e9-61a3-4d28-b64f-43d039332947",
                                    "name": "JSON Field Equals",
                                    "category": "deterministic",
                                    "description": "Checks that the output is valid JSON "
                                                   "with an expected value at a JSONPath, "
                                                   "compared type for type.",
                                    "is_active": True,
                                    "created_at": "2026-09-29T21:44:14.362617",
                                    "best_for": "One decisive field in a structured answer: "
                                                "a status, a category, a flag, an amount.",
                                    "cost": "very_fast",
                                    "limitations": "Checks one value — the first match at "
                                                   "the path; several fields need several "
                                                   "checks or a schema.",
                                    "config_fields": [
                                        {
                                            "key": "path",
                                            "label": "JSONPath",
                                            "kind": "jsonpath",
                                            "required": True,
                                            "min": None,
                                            "max": None,
                                            "integer": False,
                                            "placeholder": "$.status",
                                            "hint": "Where the value is in the answer, "
                                                    "e.g. $.items[0].sku.",
                                        },
                                        {
                                            "key": "value",
                                            "label": "Expected value (JSON)",
                                            "kind": "json",
                                            "required": True,
                                            "min": None,
                                            "max": None,
                                            "integer": False,
                                            "placeholder": '"approved"',
                                            "hint": 'A JSON value: strings in double '
                                                    'quotes, e.g. "approved"; 42, true '
                                                    'and null as they are.',
                                        },
                                    ],
                                    "engine": "json",
                                    "engine_settings": {"check": "field",
                                                        "strip_fences": True},
                                    "comparison": None,
                                },
                            ],
                        },
                    }
                }
            },
        },
        422: {
            "description": "Validation error — `test_category` is not one of the "
                           "recognized categories.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "type": "enum",
                                "loc": ["query", "test_category"],
                                "msg": "Input should be 'deterministic', 'nlp_metric' "
                                       "or 'llm_as_judge'",
                                "input": "not_a_real_category",
                                "ctx": {
                                    "expected": "'deterministic', 'nlp_metric' "
                                                "or 'llm_as_judge'"
                                },
                            }
                        ]
                    }
                }
            },
        },
    },
    response_model=list[TestTypesSchema],
)
async def get_test_types(
        test_category: Annotated[
            TestTypes,
            Query(
                description="Which broad evaluation strategy to list test types for. "
                            "One of `deterministic` (exact/regex/substring checks), "
                            "`nlp_metric` (ROUGE, BLEU, BERTScore, and similar), or "
                            "`llm_as_judge` (an LLM scoring another model's output). "
                            "Only test types in this category are returned."
            ),
        ],
        session: SessionDep,
) -> list[TestTypesSchema]: # pragma: no cover
    """List the test type catalogue entries for one evaluation category.

    Each test type describes a supported way to evaluate a test's output —
    what it measures, what it's best suited for, its relative cost, its
    known limitations, and what config it needs when assigned, if any
    (`config_fields`). These are the names accepted in `test_type_assignments`
    when creating or updating a test case or test set entry (e.g. `"ROUGE"`,
    `"BERTScore"`).

    Three read-only fields say how the worker evaluates the type: `engine`
    (which scoring engine runs it), `engine_settings` (that engine's
    parameters for this type) and `comparison` (which way a threshold-scored
    type passes; null for types with no threshold). A `threshold` field's
    `min`/`max` are the type's own native score range, which differs per
    type (0–1 for ROUGE, 0–100 for BLEU, −1 to 1 for Cosine Similarity) —
    bound the input from the descriptor, not a shared constant; a `max` of
    null means no upper bound (e.g. a word-count limit). The bounds are
    inclusive and enforced: a value outside them is a 422 when the type is
    assigned. `integer: true` means the value must be a whole number.

    Each `config_fields` entry's `kind` says what the value is: `reference`
    (read from the test's `expected_output`, never stored in `config`),
    `multiline` or `rubric` (free text), `numeric` (a number within
    `min`/`max`), `json` (JSON text), `jsonpath` (a JSONPath expression),
    `regex` (a regular expression, in the syntax of Python's `regex` library)
    or `json_schema` (a JSON Schema, written as JSON). Every kind but free
    text is checked when the type is assigned — a 422 if the value doesn't
    parse, compile, or fall within the range. Every entry
    also carries `placeholder` (an example value for the empty input) and
    `hint` (a one-line note on what the value means and what to expect), both
    null when the field has none; a hint never repeats the range or the pass
    rule, which the descriptor and `comparison` already give.

    `comparison` is one of:
    - `gte` (greater than or equal) — **higher is better**: the check passes
      when `score >= threshold`. E.g. ROUGE with threshold 0.7: a score of
      0.81 passes, 0.65 fails. Every metric type today is `gte`, since they
      all measure similarity.
    - `lte` (less than or equal) — **lower is better**: the check passes
      when `score <= threshold`. For a metric that measures mistakes, such
      as an error rate: with threshold 0.1, a score of 0.05 passes, 0.3
      fails. No type uses it yet; a future lower-is-better metric declares
      it on its catalogue row, with no code change.
    A score exactly equal to the threshold passes either way. Types that
    don't score against a threshold have `comparison: null`: Exact Match,
    Contains and Regex Match are pass/fail by nature, and an LLM-as-judge
    type passes on the judge's own verdict.

    No lookup guards apply — every value of `test_category` is a valid
    category, so this always returns a 200. If no test types exist in that
    category, the response is an empty list rather than a 404.

    Both active and inactive entries are returned; check `is_active` if you
    only want to offer currently-usable test types to a user (inactive
    entries are kept for historical/audit reference, e.g. so past test
    assignments referencing them remain resolvable, but shouldn't be offered
    for new assignments).

    Returns the matching test types ordered by `name` descending (ties
    broken by `id` descending).
    """
    return await get_test_types_by_category(test_category, session)


@router.patch(
    path="/tests/{test_case_id}",
    responses={
        200: {
            "description": "Test case updated successfully. Returns the full updated test case.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "Updated name",
                        "input": "Summarise this article in one sentence.",
                        "expected_output": "A concise one-sentence summary.",
                        "model_output": "A concise one-sentence summary.",
                        "test_type_assignments": [
                            {"name": "ROUGE", "config": {"threshold": "0.8"}},
                            {"name": "BERTScore", "config": {"threshold": "0.8"}},
                        ],
                    }
                }
            },
        },
        404: {
            "description": "Test case not found.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test with id a1b2c3d4-e5f6-7890-abcd-ef1234567890 not found"
                    }
                }
            },
        },
        422: {
            "description": (
                "Validation error — either an unknown field was sent in the request body, "
                "one or more test type names are not in the catalogue, an assignment "
                "is missing a required config field, a config value isn't valid for "
                "its field (a number out of range, a pattern that doesn't compile, "
                "JSON, a JSONPath or a JSON Schema that doesn't parse) or an "
                "`answer_path` doesn't parse, or a reference-required type "
                "(e.g. Exact Match, ROUGE) is left with no `expected_output` once this "
                "update is applied — considering both the request and whatever the test "
                "case already had for any field this request doesn't touch."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "unknown_field": {
                            "summary": "Unknown field in body",
                            "value": {
                                "detail": [
                                    {
                                        "type": "extra_forbidden",
                                        "loc": ["body", "id"],
                                        "msg": "Extra inputs are not permitted",
                                    }
                                ]
                            },
                        },
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
    response_model=CreateTestCaseResponse,
)
async def update_test(
        test_case_id: uuid.UUID,
        request: ModifyTestCaseRequest,
        session: SessionDep,
) -> CreateTestCaseResponse: # pragma: no cover
    """Partially update a test case by ID.

    Only the fields included in the request body are updated — omitted fields are left unchanged.

    | Field | Sent with a value | Sent as `null` | Left out |
    |---|---|---|---|
    | `expected_output`, `model_output` | set | **cleared** | unchanged |
    | `name`, `input` | set | unchanged (neither can be empty) | unchanged |
    | `test_type_assignments` | replaces the whole list (`[]` removes all) | unchanged | unchanged |

    Each assignment has a `label`, unique within the test (ignoring letter case), so a
    type can be assigned more than once — two Contains, two JSON Field Equals on different
    paths. A label sent is kept as it is; one left out is the type's name, numbered past
    every label in use (`Contains`, `Contains 2`). When replacing the list, send back the
    labels a GET returned: each check keeps its identity, and a run's `results`, keyed by
    label, stay comparable across runs. Duplicate labels are a 422.

    Clearing `model_output` means runs of this test ask the application under test for
    the answer. Clearing `expected_output` is refused (422) while a type that needs it
    (e.g. Exact Match, ROUGE) stays assigned.

    Unknown body fields are rejected with a 422 — the schema uses `extra="forbid"` to
    prevent silently ignoring misplaced fields such as `id`.

    Returns the full updated test case so the client does not need a follow-up GET to refresh.

    Returns a 404 if no test case with the given ID exists.
    Returns a 422 if any unknown field is sent, if any test type name is not in the
    catalogue, if a config value isn't valid for its field (see `GET /tests/types`'
    field kinds) or an assignment's `answer_path` doesn't parse, or if — considering
    the effective state after this update — a type
    requiring a reference is assigned while `expected_output` is empty.
    """
    return await modify_test_by_id(test_case_id, request, session)


@router.get(
    path="/tests/{test_case_id}",
    responses={
        200: {
            "description": "Test case found.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "My test case",
                        "input": "Summarise this article in one sentence.",
                        "expected_output": "A concise one-sentence summary.",
                        "model_output": "A concise one-sentence summary.",
                        "test_type_assignments": [
                            {"name": "ROUGE", "config": {"threshold": "0.8"}},
                            {"name": "BERTScore", "config": {"threshold": "0.8"}},
                        ],
                    }
                }
            },
        },
        404: {
            "description": "Test case not found.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Test with id a1b2c3d4-e5f6-7890-abcd-ef1234567890 not found"
                    }
                }
            },
        },
    },
    response_model=CreateTestCaseResponse,
)
async def get_specific_test(
        test_case_id: uuid.UUID,
        session: SessionDep,
) -> CreateTestCaseResponse: # pragma: no cover
    """Retrieve a single test case by ID.

    Returns the test case with all fields and its assigned test type assignments.

    Returns a 404 if no test case with the given ID exists.
    """
    return await get_test_case_by_id(test_case_id, session)


@router.get(
    path="/tests",
    responses={
        200: {
            "description": "Paginated list of test cases.",
            "content": {
                "application/json": {
                    "example": {
                        "test_cases": [
                            {
                                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                "name": "My test case",
                                "input": "Summarise this article in one sentence.",
                                "expected_output": "A concise one-sentence summary.",
                                "model_output": "A concise one-sentence summary.",
                                "test_type_assignments": [
                                    {"name": "ROUGE", "config": {"threshold": "0.8"}},
                                    {"name": "BERTScore", "config": {"threshold": "0.8"}},
                                ],
                            }
                        ],
                        "total": 1,
                        "offset": 0,
                        "limit": 100,
                    }
                }
            },
        }
    },
    response_model=PaginatedTestCases
)
async def get_all_tests(
        session: SessionDep,
        offset: int = Query(default=0, description="Number of records to skip."),
        limit: int = Query(default=100, description="Maximum number of records to return."),
) -> PaginatedTestCases: # pragma: no cover
    """Return a paginated list of all test cases.

    Use `offset` and `limit` to page through results. The response always includes
    `total` — the count of all tests in the database — so clients can determine
    whether more pages exist.
    """
    return await get_all_created_tests(session, offset, limit)


@router.post(
    path="/tests",
    responses={
        200: {
            "description": "Test case created successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "name": "My test case",
                        "input": "Summarise this article in one sentence.",
                        "expected_output": "A concise one-sentence summary.",
                        "model_output": None,
                        "test_type_assignments": [
                            {"name": "ROUGE", "config": {"threshold": "0.8"}},
                            {"name": "BERTScore", "config": {"threshold": "0.8"}},
                        ],
                    }
                }
            },
        },
        422: {
            "description": "One or more test type names are not in the catalogue, "
                           "an assignment is missing a required config field, a "
                           "config value isn't valid for its field (out of range, "
                           "doesn't compile or doesn't parse) or an `answer_path` "
                           "doesn't parse, or a "
                           "type requiring a reference (e.g. Exact Match, ROUGE) is "
                           "assigned while `expected_output` is empty.",
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
    response_model=CreateTestCaseResponse,
)
async def create_test_manually(
        request: CreateTestCaseRequest,
        session: SessionDep) -> CreateTestCaseResponse:  # pragma: no cover
    """Create a new test case manually.

    Persists a single test definition with its input and optional reference outputs.
    The test is immediately available for standalone execution or inclusion in a test set.

    `name` defaults to a fresh UUID if omitted.
    `expected_output` is what the answer should be. It's required by the test types that
    compare against it — those with a `reference` field in `config_fields`, e.g. Exact
    Match, ROUGE, Correctness — and can be left `null` when none is assigned.
    `model_output` is the answer your application gave, if you recorded it: runs then score
    it as it is. Leave it `null` to have every run ask the application under test for the
    answer instead; the answer a run scored is recorded on the run (`evaluated_output`),
    never written back to the test.
    `test_type_assignments` is an optional list of evaluation strategies to assign, each with
    any config it needs. Each name must exist in the test types catalogue and satisfy that
    type's required config fields — a 422 is returned otherwise. A type with a required
    reference field (e.g. Exact Match, ROUGE) also requires a non-empty `expected_output`
    on this same request — a 422 is returned if one isn't provided.
    Each assignment can also set `answer_path` — a JSONPath naming the part of the
    application's reply that check reads instead of the default answer, e.g.
    `$.stop_reason` (or, on a recorded answer that is JSON, the part of it). Leave it out
    for almost every check. Every config value must be valid for its field — a number
    within its range, a pattern that compiles, JSON, a JSONPath or a JSON Schema that
    parses — and an `answer_path` must parse: a 422 otherwise, listing every problem.

    On success, returns the created test case with its generated `id` and all input fields.
    """

    return await create_new_test(request, session)


@router.post(
    path="/tests/from-dataset",
    responses={
        200: {
            "description": "Test cases created successfully for every row in the dataset.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "test_cases": [
                            {"id": "11111111-1111-1111-1111-111111111111"},
                            {"id": "22222222-2222-2222-2222-222222222222"},
                        ],
                    }
                }
            },
        },
        404: {
            "description": "Dataset not found, or the dataset has no rows.",
            "content": {
                "application/json": {
                    "examples": {
                        "dataset_not_found": {
                            "summary": "Dataset not found",
                            "value": {
                                "detail": (
                                    "Dataset with id"
                                    " a1b2c3d4-e5f6-7890-abcd-ef1234567890 not found"
                                )
                            },
                        },
                        "no_rows": {
                            "summary": "Dataset has no rows",
                            "value": {
                                "detail": (
                                    "No Dataset Rows were found with dataset id"
                                    " a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                                )
                            },
                        },
                    }
                }
            },
        },
        422: {
            "description": "One or more test type names are not in the catalogue, "
                           "an assignment is missing a required config field, a "
                           "config value isn't valid for its field (out of range, "
                           "doesn't compile or doesn't parse) or an `answer_path` "
                           "doesn't parse, or a "
                           "type requiring a reference (e.g. Exact Match, ROUGE) is "
                           "assigned while one or more dataset rows have an empty "
                           "`expected_output`.",
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
                            "summary": "Reference-required type with rows missing expected_output",
                            "value": {
                                "detail": (
                                    "Test types ['Exact Match'] require a non-empty "
                                    "expected_output, but Dataset Rows with ids "
                                    "['11111111-1111-1111-1111-111111111111'] have none"
                                )
                            },
                        },
                    }
                }
            },
        },
    },
    response_model=CreateTestCaseFromDatasetResponse,
)
async def create_test_from_dataset(
        request: CreateTestCaseFromDatasetRequest,
        session: SessionDep) -> CreateTestCaseFromDatasetResponse:  # pragma: no cover
    """Create test cases in bulk from all rows of an existing dataset.

    Each row in the dataset becomes a separate test case, named "New Test <n>"
    (dataset rows have no name of their own to reuse), numbered globally across
    every test in the system — re-running this endpoint continues the numbering
    from the highest "New Test <n>" that already exists, rather than restarting
    at 1 and duplicating a name already in use. All created tests share the same
    optional list of evaluation strategies (`test_type_assignments`).

    Each test copies its row's prompt, expected output and model output. A row whose
    `model_output` is blank (empty or only whitespace) makes a test with no recorded
    answer (`model_output` null), so its runs ask the application under test; any other
    `model_output` is the test's recorded answer, kept exactly as written.

    `test_type_assignments` is an optional list of evaluation strategies to assign, each with
    any config it needs. Each name must exist in the test types catalogue and satisfy that
    type's required config fields — a 422 is returned otherwise. A type with a required
    reference field (e.g. Exact Match, ROUGE) also requires every row in the dataset to
    already have a non-empty `expected_output` — a 422 is returned listing every row that
    doesn't, all-or-nothing.
    Each assignment can also set `answer_path` — a JSONPath naming the part of the
    application's reply that check reads instead of the default answer, e.g.
    `$.stop_reason` (or, on a recorded answer that is JSON, the part of it). Leave it out
    for almost every check. Every config value must be valid for its field — a number
    within its range, a pattern that compiles, JSON, a JSONPath or a JSON Schema that
    parses — and an `answer_path` must parse: a 422 otherwise, listing every problem.

    Returns a 404 if the dataset does not exist or has no rows.

    Returns the dataset ID and the IDs of all created test cases.
    """

    return await create_new_test_from_dataset(request, session)


@router.delete(
    "/tests",
    responses={
        200: {"description": "Test cases deleted successfully."},
        404: {
            "description": "One or more test case IDs were not found.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "Tests with ids ['a1b2c3d4-e5f6-7890-abcd-ef1234567890'] not found"
                        )
                    }
                }
            },
        },
        409: {
            "description": "One or more tests are linked to a test "
                           "set or test run and cannot be deleted.",
            "content": {
                "application/json": {
                    "examples": {
                        "linked_to_test_set": {
                            "summary": "Test linked to a test set",
                            "value": {
                                "detail": (
                                    "Tests with ids ['a1b2c3d4-e5f6-7890-abcd-ef1234567890']"
                                    " cannot be deleted because they are linked to test sets"
                                )
                            },
                        },
                        "linked_to_test_run": {
                            "summary": "Test linked to a test run",
                            "value": {
                                "detail": (
                                    "Tests with ids ['a1b2c3d4-e5f6-7890-abcd-ef1234567890']"
                                    " cannot be deleted because they are linked to test runs"
                                )
                            },
                        },
                    }
                }
            },
        },
    },
)
async def delete_test(
        request: list[TestCaseID],
        session: SessionDep) -> dict: # pragma: no cover
    """Delete one or more test cases by ID.

    Accepts a list of test case IDs and deletes them in a single bulk operation.

    Returns a 404 if any of the requested IDs do not exist.

    Returns a 409 if any test cannot be safely deleted:
    - if the test is included in a test set, it must be unlinked from the set first.
    - if the test has been run (standalone runs), it's kept: its runs record what they
      scored and refer back to it.
    """
    await delete_test_by_id(request, session)
    return {}
