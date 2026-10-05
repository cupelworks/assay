# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from assay.schemas import CreateTestCaseRequest, TestTypeAssignment
from assay.services import create_new_test, create_new_test_from_dataset
from assay.services.tests._common import (
    _check_reference_required_types_have_expected_output_for_rows_or_422,
    _check_reference_required_types_have_expected_output_or_422,
    _validate_test_type_assignments,
)
from assay.services.tests.create_new_test import _name_from_prompt

name = "Test Name"
model_input = "My Input"
model_output = "The model output"
expected_output = "The expected model output"


def _catalogue_row(type_name, config_fields):
    row = MagicMock()
    row.name = type_name
    row.config_fields = config_fields
    return row


def test_create_new_test_without_name():
    request = CreateTestCaseRequest(
        input=model_input,
        model_output=model_output,
        expected_output=expected_output
    )
    uuid.UUID(request.name)

    mock_session = AsyncMock()

    response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    uuid.UUID(str(response.id))
    uuid.UUID(response.name)
    assert response.input == model_input
    assert response.model_output == model_output
    assert response.expected_output == expected_output


def test_create_new_test_with_name():
    request = CreateTestCaseRequest(
        name=name,
        input=model_input,
        model_output=model_output,
        expected_output=expected_output
    )
    with pytest.raises(ValueError):
        uuid.UUID(request.name)

    mock_session = AsyncMock()

    response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    assert response.name == name


def test_create_new_test_without_model_expected_output():
    request = CreateTestCaseRequest(
        name=name,
        input=model_input,
        model_output=None,
        expected_output=None
    )

    mock_session = AsyncMock()

    response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    assert response.model_output is None
    assert response.expected_output is None


def test_create_new_test_with_test_names():
    request = CreateTestCaseRequest(
        input=model_input,
        model_output=model_output,
        expected_output=expected_output,
        test_type_assignments=[
            TestTypeAssignment(name="ROUGE"),
            TestTypeAssignment(name="BERTScore"),
        ],
    )

    mock_session = AsyncMock()

    with patch(
            "assay.services.tests.create_new_test._validate_test_type_assignments",
            new=AsyncMock()
    ):
        response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    assert response.test_type_assignments == [  # in label order
        TestTypeAssignment(name="BERTScore", label="BERTScore"),
        TestTypeAssignment(name="ROUGE", label="ROUGE"),
    ]


def test_create_new_test_raises_422_for_reference_required_type_with_no_expected_output():
    request = CreateTestCaseRequest(
        input=model_input,
        expected_output=None,
        test_type_assignments=[TestTypeAssignment(name="Exact Match")],
    )

    mock_session = AsyncMock()
    catalogue_row = MagicMock()
    catalogue_row.name = "Exact Match"
    catalogue_row.config_fields = [
        {"key": "reference", "label": "Expected output", "kind": "reference", "required": True}
    ]
    mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[catalogue_row]))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_not_called()
    assert exc.value.status_code == 422
    assert "Exact Match" in str(exc.value.detail)


def test_validate_raises_for_unknown_name():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(
            mock_session, [TestTypeAssignment(name="ROUGE")]
        ))
    assert exc.value.status_code == 422


def test_validate_passes_for_known_name_no_config_needed():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("ROUGE", [
            {"key": "reference", "label": "Reference text", "kind": "reference", "required": True}
        ])])
    )

    # ROUGE's only field is kind "reference" — never checked against config, so
    # a bare assignment with no config passes.
    asyncio.run(_validate_test_type_assignments(
        mock_session, [TestTypeAssignment(name="ROUGE")]
    ))  # no raise


def test_validate_raises_for_missing_required_config():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Regex Match", [
            {"key": "pattern", "label": "Regex pattern", "kind": "multiline", "required": True}
        ])])
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(
            mock_session, [TestTypeAssignment(name="Regex Match")]
        ))
    assert exc.value.status_code == 422
    assert "pattern" in str(exc.value.detail)


def test_validate_passes_with_required_config_provided():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Regex Match", [
            {"key": "pattern", "label": "Regex pattern", "kind": "multiline", "required": True}
        ])])
    )

    asyncio.run(_validate_test_type_assignments(
        mock_session,
        [TestTypeAssignment(name="Regex Match", config={"pattern": "^\\d+$"})],
    ))  # no raise


def test_validate_passes_for_optional_config_omitted():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Toxicity", [
            {"key": "rubric", "label": "Custom toxicity rubric",
             "kind": "rubric", "required": False}
        ])])
    )

    # rubric is optional — omitting config entirely still passes.
    asyncio.run(_validate_test_type_assignments(
        mock_session, [TestTypeAssignment(name="Toxicity")]
    ))  # no raise


def test_validate_raises_for_missing_required_threshold():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("ROUGE", [
            {"key": "reference", "label": "Reference text",
             "kind": "reference", "required": True},
            {"key": "threshold", "label": "Minimum score to pass",
             "kind": "numeric", "required": True},
        ])])
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(
            mock_session, [TestTypeAssignment(name="ROUGE")]
        ))
    assert exc.value.status_code == 422
    assert "threshold" in str(exc.value.detail)


# -- numeric fields: a number within the field's range, whole when it says so --


def _one_field_session(type_name, field):
    session = AsyncMock()
    session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row(type_name, [field])])
    )
    return session


def _validation_detail(type_name, field, value):
    """The 422 detail for one value of one field, or None when it's accepted."""
    try:
        asyncio.run(_validate_test_type_assignments(
            _one_field_session(type_name, field),
            [TestTypeAssignment(name=type_name, config={field["key"]: value})],
        ))
    except HTTPException as exc:
        assert exc.status_code == 422
        return exc.detail
    return None


_THRESHOLD = {"key": "threshold", "label": "Minimum score to pass", "kind": "numeric",
              "required": True, "min": 0.0, "max": 1.0}
_MAX_WORDS = {"key": "max", "label": "Maximum words", "kind": "numeric", "required": True,
              "min": 0.0, "max": None, "integer": True}


@pytest.mark.parametrize("value", ["0", "1", "0.7", " 0.5 ", "1e-1"])
def test_validate_passes_a_threshold_within_its_range_bounds_included(value):
    assert _validation_detail("ROUGE", _THRESHOLD, value) is None


@pytest.mark.parametrize("value", ["not a number", "70%", "nan", "inf"])
def test_validate_raises_for_a_threshold_that_is_not_a_number(value):
    assert _validation_detail("ROUGE", _THRESHOLD, value) == (
        "'ROUGE' config field 'threshold' is not a number")


@pytest.mark.parametrize("value", ["70", "-0.1", "1.01"])
def test_validate_raises_for_a_threshold_outside_its_range(value):
    # 70 is the classic mistake: a percentage on a 0-1 scale never passes
    assert _validation_detail("ROUGE", _THRESHOLD, value) == (
        "'ROUGE' config field 'threshold' must be between 0 and 1")


def test_validate_names_the_one_bound_a_half_open_range_has():
    assert _validation_detail("Word Count Limit", _MAX_WORDS, "-1") == (
        "'Word Count Limit' config field 'max' must be at least 0")
    upper_only = {**_THRESHOLD, "min": None, "max": 100.0}
    assert _validation_detail("BLEU", upper_only, "101") == (
        "'BLEU' config field 'threshold' must be at most 100")


def test_validate_takes_any_number_when_the_field_has_no_range():
    unbounded = {**_THRESHOLD, "min": None, "max": None}
    assert _validation_detail("ROUGE", unbounded, "-12.5") is None


@pytest.mark.parametrize("value,accepted", [("100", True), ("100.0", True), ("0", True),
                                            ("10.5", False)])
def test_validate_wants_a_whole_number_when_the_field_says_integer(value, accepted):
    detail = _validation_detail("Word Count Limit", _MAX_WORDS, value)
    if accepted:
        assert detail is None
    else:
        assert detail == "'Word Count Limit' config field 'max' is not a whole number"


def test_validate_never_echoes_the_submitted_value():
    detail = _validation_detail("ROUGE", _THRESHOLD, "secret-looking 42")
    assert "secret-looking" not in detail


# -- regex fields: compiled with the worker's own library --

_PATTERN = {"key": "pattern", "label": "Regex pattern", "kind": "regex", "required": True}


@pytest.mark.parametrize("pattern", [r"^\d{3}-\d{4}$", "(?i)paris", r"\p{Lu}+", "(?<=a+)b"])
def test_validate_passes_a_pattern_the_regex_library_compiles(pattern):
    # \p{Lu} and the variable-width lookbehind compile with `regex`, not with
    # the stdlib `re`: the check uses what the worker uses
    assert _validation_detail("Regex Match", _PATTERN, pattern) is None


@pytest.mark.parametrize("pattern,reason", [
    ("[A-Z+", "unterminated character set at position 5"),
    ("[z-a]", "bad character range at position 4"),
    ("a{99999999999}", "repeat count too big at position 2"),
])
def test_validate_raises_for_a_pattern_that_does_not_compile(pattern, reason):
    assert _validation_detail("Regex Match", _PATTERN, pattern) == (
        f"'Regex Match' config field 'pattern' is not a valid regex pattern: {reason}")


# -- json_schema fields: valid JSON and a valid JSON Schema --

_SCHEMA = {"key": "schema", "label": "JSON Schema", "kind": "json_schema", "required": True}


@pytest.mark.parametrize("schema", [
    '{"type": "object", "required": ["status"]}',
    '{"$schema": "http://json-schema.org/draft-07/schema#", "type": "array"}',
    "true",
])
def test_validate_passes_a_valid_json_schema(schema):
    assert _validation_detail("Matches JSON Schema", _SCHEMA, schema) is None


def test_validate_raises_for_a_schema_that_is_not_json():
    assert _validation_detail("Matches JSON Schema", _SCHEMA, "{type: object}").startswith(
        "'Matches JSON Schema' config field 'schema' is not valid JSON: ")


def test_validate_raises_for_json_that_is_not_a_valid_schema():
    assert _validation_detail("Matches JSON Schema", _SCHEMA, '{"type": "nope"}') == (
        "'Matches JSON Schema' config field 'schema' is not a valid JSON Schema: "
        "'nope' is not valid under any of the given schemas")


@pytest.mark.parametrize("schema", ["42", "null", '"object"', "[1]"])
def test_validate_raises_for_a_schema_that_is_not_an_object(schema):
    detail = _validation_detail("Matches JSON Schema", _SCHEMA, schema)
    assert detail.startswith("'Matches JSON Schema' config field 'schema' is not a valid "
                             "JSON Schema: ")


# -- json / jsonpath fields: checked to parse on save --

_JSON_FIELD_EQUALS = [
    {"key": "path", "label": "JSONPath", "kind": "jsonpath", "required": True},
    {"key": "value", "label": "Expected value (JSON)", "kind": "json", "required": True},
]


def _json_field_equals_session():
    session = AsyncMock()
    session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("JSON Field Equals", _JSON_FIELD_EQUALS)])
    )
    return session


@pytest.mark.parametrize("value", ['"approved"', "42", "true", "null", '{"a": [1, 2]}'])
def test_validate_passes_a_json_field_that_parses(value):
    asyncio.run(_validate_test_type_assignments(
        _json_field_equals_session(),
        [TestTypeAssignment(name="JSON Field Equals",
                            config={"path": "$.status", "value": value})],
    ))  # no raise


def test_validate_raises_for_a_json_field_that_does_not_parse():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(
            _json_field_equals_session(),
            [TestTypeAssignment(name="JSON Field Equals",
                                config={"path": "$.status", "value": "approved"})],
        ))

    assert exc.value.status_code == 422
    assert exc.value.detail == (
        "'JSON Field Equals' config field 'value' is not valid JSON: "
        "Expecting value: line 1 column 1 (char 0)"
    )


def test_validate_raises_for_a_jsonpath_field_that_does_not_parse():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(
            _json_field_equals_session(),
            [TestTypeAssignment(name="JSON Field Equals",
                                config={"path": "$[", "value": '"approved"'})],
        ))

    assert exc.value.detail.startswith(
        "'JSON Field Equals' config field 'path' is not a valid JSONPath: ")


def test_validate_lists_every_problem_together():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(
            _json_field_equals_session(),
            [TestTypeAssignment(name="JSON Field Equals",
                                config={"path": "$[", "value": "approved"})],
        ))

    problems = exc.value.detail.split("; ")
    assert [p.split(" is not")[0] for p in problems] == [
        "'JSON Field Equals' config field 'path'",
        "'JSON Field Equals' config field 'value'",
    ]


def test_validate_checks_an_optional_json_field_only_when_given():
    session = AsyncMock()
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[_catalogue_row(
        "Optional JSON", [{"key": "extra", "label": "Extra", "kind": "json", "required": False}],
    )]))

    asyncio.run(_validate_test_type_assignments(
        session, [TestTypeAssignment(name="Optional JSON")]))  # left out: no raise
    with pytest.raises(HTTPException):
        asyncio.run(_validate_test_type_assignments(
            session, [TestTypeAssignment(name="Optional JSON", config={"extra": "{oops"})]))


# -- _check_reference_required_types_have_expected_output_or_422 --
# _validate_test_type_assignments deliberately never checks a "reference"
# field (it resolves from expected_output, not config), so this
# guard exists specifically to catch the gap that leaves: an assignment
# needing a reference with no expected_output anywhere to supply one.

def test_check_reference_raises_when_expected_output_missing():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Exact Match", [
            {"key": "reference", "label": "Expected output",
             "kind": "reference", "required": True},
        ])])
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_check_reference_required_types_have_expected_output_or_422(
            mock_session, [TestTypeAssignment(name="Exact Match")], None
        ))
    assert exc.value.status_code == 422
    assert "Exact Match" in str(exc.value.detail)


def test_check_reference_passes_when_expected_output_present():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Exact Match", [
            {"key": "reference", "label": "Expected output",
             "kind": "reference", "required": True},
        ])])
    )

    asyncio.run(_check_reference_required_types_have_expected_output_or_422(
        mock_session, [TestTypeAssignment(name="Exact Match")], "some answer"
    ))  # no raise


def test_check_reference_passes_for_types_with_no_reference_field():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Regex Match", [
            {"key": "pattern", "label": "Regex pattern",
             "kind": "multiline", "required": True},
        ])])
    )

    # Regex Match has no "reference" field at all — missing expected_output
    # is irrelevant to it, nothing to raise on.
    asyncio.run(_check_reference_required_types_have_expected_output_or_422(
        mock_session, [TestTypeAssignment(name="Regex Match", config={"pattern": "x"})], None
    ))  # no raise


# -- _check_reference_required_types_have_expected_output_for_rows_or_422 --

def test_check_reference_for_rows_raises_listing_every_offending_row():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Exact Match", [
            {"key": "reference", "label": "Expected output",
             "kind": "reference", "required": True},
        ])])
    )
    good_row = MagicMock(id=uuid.uuid4(), expected_output="has one")
    bad_row_1 = MagicMock(id=uuid.uuid4(), expected_output=None)
    bad_row_2 = MagicMock(id=uuid.uuid4(), expected_output="")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_check_reference_required_types_have_expected_output_for_rows_or_422(
            mock_session, [TestTypeAssignment(name="Exact Match")],
            [good_row, bad_row_1, bad_row_2],
        ))
    assert exc.value.status_code == 422
    assert str(bad_row_1.id) in str(exc.value.detail)
    assert str(bad_row_2.id) in str(exc.value.detail)
    assert str(good_row.id) not in str(exc.value.detail)


def test_check_reference_for_rows_passes_when_every_row_has_one():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Exact Match", [
            {"key": "reference", "label": "Expected output",
             "kind": "reference", "required": True},
        ])])
    )
    rows = [MagicMock(id=uuid.uuid4(), expected_output="present") for _ in range(3)]

    asyncio.run(_check_reference_required_types_have_expected_output_for_rows_or_422(
        mock_session, [TestTypeAssignment(name="Exact Match")], rows
    ))  # no raise


# -- create_new_test_from_dataset

def _get_mock_request_with_id():
    # returns both the mock and the raw dataset id string
    mock_request_id = str(uuid.uuid4())
    return MagicMock(id=mock_request_id), mock_request_id


def _patch_rows(*rows):
    # shorthand for mocking the DB call that returns dataset rows
    return patch(
        "assay.services.tests.create_new_test._get_all_rows_or_404",
        new=AsyncMock(return_value=list(rows)),
    )


def _patch_validate_test_type_assignments():
    return patch("assay.services.tests.create_new_test._validate_test_type_assignments")


def _patch_check_reference_required_types_for_rows():
    # shorthand for tests that don't care about the expected_output/reference
    # check itself — see test_create_new_test.py's reference-specific tests
    # for that
    return patch(
        "assay.services.tests.create_new_test"
        "._check_reference_required_types_have_expected_output_for_rows_or_422",
        new=AsyncMock(),
    )


def _patch_next_new_test_number(start=1):
    # shorthand for tests that don't care about the numbering itself —
    # see test_names_start_at_1_when_none_exist_yet and
    # test_names_continue_globally_from_existing_new_test_names for that
    return patch(
        "assay.services.tests.create_new_test._next_new_test_number",
        new=AsyncMock(return_value=start),
    )


def _get_rows():
    return MagicMock(
        input="first", expected_output="y", model_output="z", id=uuid.uuid4()), MagicMock(
        input="second", expected_output="y", model_output="z", id=uuid.uuid4()), MagicMock(
        input="third", expected_output="y", model_output="z", id=uuid.uuid4()),

def test_row_exist_no_test_type_assignments():
    mock_request, mock_request_id = _get_mock_request_with_id()
    # [] is falsy, so the `if request.test_type_assignments:` branch is skipped entirely
    mock_request.test_type_assignments = []

    mock_session = AsyncMock()

    # _patch_validate_test_type_assignments is captured so we can assert it was never called
    with _patch_rows(*_get_rows()), \
            _patch_next_new_test_number(), \
            _patch_validate_test_type_assignments() as mock_validate_test_type_assignments:
        result = asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    # validation must be skipped when no test type assignments are provided
    mock_validate_test_type_assignments.assert_not_called()
    mock_session.commit.assert_called_once()
    # the response must carry back the original dataset id
    assert result.id == UUID(mock_request_id)
    # 3 rows in → 3 TestModels created → 3 ids in the response
    assert len(result.test_cases) == 3
    # add_all is called twice: first for tests, then for test_types.
    # [1] = second call, [0] = positional args tuple, [0] = the list passed in.
    # with no test_type_assignments, no TestTypeAssignmentModels should be created.
    assert mock_session.add_all.call_args_list[1][0][0] == []


def test_a_blank_model_output_makes_a_test_with_no_recorded_answer():
    mock_request, _ = _get_mock_request_with_id()
    mock_request.test_type_assignments = []
    rows = [MagicMock(input=text, expected_output="y", model_output=answer, id=uuid.uuid4())
            for text, answer in (("empty", ""), ("spaces", "  \n "), ("recorded", " z "),
                                 ("none", None))]
    mock_session = AsyncMock()

    with _patch_rows(*rows), _patch_next_new_test_number():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    tests = mock_session.add_all.call_args_list[0][0][0]
    assert [(t.input, t.model_output) for t in tests] == [
        ("empty", None), ("spaces", None), ("recorded", " z "), ("none", None)]


def test_correct_number_of_test_type_assignments():
    mock_request, mock_request_id = _get_mock_request_with_id()
    # 2 test types will be assigned to each test
    mock_request.test_type_assignments = [
        TestTypeAssignment(name="ROUGE"),
        TestTypeAssignment(name="BERTScore"),
    ]

    mock_session = AsyncMock()

    # 3 rows → 3 TestModels, each assigned 2 test types → 6 TestTypeAssignmentModels
    with _patch_rows(*_get_rows()), \
            _patch_next_new_test_number(), \
            _patch_validate_test_type_assignments(), \
            _patch_check_reference_required_types_for_rows():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    # first add_all call is for tests: one per row
    assert len(mock_session.add_all.call_args_list[0][0][0]) == 3
    # second add_all call is for test_types: cartesian product of tests × assignments (3 × 2)
    assert len(mock_session.add_all.call_args_list[1][0][0]) == 6


def test_correct_mapping():
    mock_request, mock_request_id = _get_mock_request_with_id()

    mock_session = AsyncMock()

    with _patch_rows(*_get_rows()), \
            _patch_next_new_test_number(), \
            _patch_validate_test_type_assignments(), \
            _patch_check_reference_required_types_for_rows():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    # mapping is the same for every row, so checking one is enough.
    # [0] = first add_all call (tests), [0] = positional args, [0] = the list, [0] = first TestModel
    single_test = mock_session.add_all.call_args_list[0][0][0][0]
    assert single_test.input == "first"
    assert single_test.expected_output == "y"
    assert single_test.model_output == "z"


def test_names_start_at_1_when_none_exist_yet():
    mock_request, _ = _get_mock_request_with_id()

    mock_session = AsyncMock()
    mock_session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with _patch_rows(*_get_rows()), \
            _patch_validate_test_type_assignments(), \
            _patch_check_reference_required_types_for_rows():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    created_tests = mock_session.add_all.call_args_list[0][0][0]
    assert [test.name for test in created_tests] == [
        "New Test 1", "New Test 2", "New Test 3"
    ]


def test_names_continue_globally_from_existing_new_test_names():
    mock_request, _ = _get_mock_request_with_id()

    mock_session = AsyncMock()
    # a prior import already created "New Test 1"/"New Test 2"; a manually
    # renamed test that doesn't match the pattern must be ignored, not
    # counted, and must not derail the max() calculation
    mock_session.scalars.return_value = MagicMock(all=MagicMock(
        return_value=["New Test 1", "New Test 2", "Renamed by a user"]
    ))

    with _patch_rows(*_get_rows()), \
            _patch_validate_test_type_assignments(), \
            _patch_check_reference_required_types_for_rows():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    created_tests = mock_session.add_all.call_args_list[0][0][0]
    assert [test.name for test in created_tests] == [
        "New Test 3", "New Test 4", "New Test 5"
    ]


def test_database_not_found():
    mock_session = AsyncMock()
    mock_request, _ = _get_mock_request_with_id()

    # force _get_dataset_or_404 to raise 404 via side_effect — execution stops there,
    # the session and all subsequent steps are never reached
    with patch(
            "assay.services.tests.create_new_test._get_dataset_or_404",
            new=AsyncMock(side_effect=HTTPException(status_code=404)),
    ), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    assert exc.value.status_code == 404


def test_no_dataset_rows():
    mock_session = AsyncMock()
    # make session.scalars(...).all() return [] so the real _get_all_rows_or_404 raises 404
    mock_session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    mock_request, _ = _get_mock_request_with_id()

    # safety net: _get_dataset_or_404 would pass anyway since AsyncMock is always truthy,
    # but patching makes the intent explicit and avoids relying on that side effect
    with patch("assay.services.tests.create_new_test._get_dataset_or_404"), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    assert exc.value.status_code == 404


def test_unknown_test_type_name():
    mock_session = AsyncMock()
    # simulate the test types catalogue returning no matches — _validate_test_type_assignments
    # computes the difference between requested names and found names, and raises 422 if non-empty
    mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    mock_request, _ = _get_mock_request_with_id()
    mock_request.test_type_assignments = [TestTypeAssignment(name="SHOULD FAIL")]

    # _get_dataset_or_404 is patched as a safety net (AsyncMock is always truthy)
    # _patch_rows lets execution reach _validate_test_type_assignments, where the 422 is raised
    with patch("assay.services.tests.create_new_test._get_dataset_or_404"), \
            _patch_rows(*_get_rows()), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    assert exc.value.status_code == 422


def test_dataset_import_422_when_any_row_missing_expected_output_for_reference_required_type():
    mock_request, _ = _get_mock_request_with_id()
    mock_request.test_type_assignments = [TestTypeAssignment(name="Exact Match")]

    good_row = MagicMock(input="ok", expected_output="present",
                         model_output="z", id=uuid.uuid4())
    bad_row = MagicMock(input="missing ref", expected_output=None,
                        model_output="z", id=uuid.uuid4())

    mock_session = AsyncMock()
    catalogue_row = MagicMock()
    catalogue_row.name = "Exact Match"
    catalogue_row.config_fields = [
        {"key": "reference", "label": "Expected output", "kind": "reference", "required": True}
    ]
    mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[catalogue_row]))

    with _patch_rows(good_row, bad_row), \
            _patch_validate_test_type_assignments(), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    mock_session.commit.assert_not_called()
    assert exc.value.status_code == 422
    assert str(bad_row.id) in str(exc.value.detail)
    assert str(good_row.id) not in str(exc.value.detail)


# -- answer_path: which part of the answer a check reads --


def test_create_stores_each_assignments_answer_path():
    request = CreateTestCaseRequest(
        input=model_input,
        test_type_assignments=[
            TestTypeAssignment(name="JSON Field Equals", answer_path="$.stop_reason",
                               config={"path": "$", "value": '"end_turn"'}),
            TestTypeAssignment(name="Contains", config={"substring": "refund"}),
        ],
    )
    mock_session = AsyncMock()
    mock_session.add_all = MagicMock()

    with patch("assay.services.tests.create_new_test._validate_test_type_assignments",
               new=AsyncMock()), \
            patch("assay.services.tests.create_new_test."
                  "_check_reference_required_types_have_expected_output_or_422", new=AsyncMock()):
        response = asyncio.run(create_new_test(request, mock_session))

    stored = mock_session.add_all.call_args.args[0]
    assert [(a.test_type_name, a.answer_path) for a in stored] == [  # in label order
        ("Contains", None), ("JSON Field Equals", "$.stop_reason")]
    assert [a.answer_path for a in response.test_type_assignments] == [None, "$.stop_reason"]


def _contains_session():
    session = AsyncMock()
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[_catalogue_row(
        "Contains", [{"key": "substring", "label": "Required substring", "kind": "multiline",
                      "required": True}],
    )]))
    return session


def test_validate_passes_a_valid_answer_path():
    asyncio.run(_validate_test_type_assignments(_contains_session(), [
        TestTypeAssignment(name="Contains", config={"substring": "x"},
                           answer_path="$.result.category"),
    ]))  # no raise


def test_validate_raises_for_an_answer_path_that_does_not_parse():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(_contains_session(), [
            TestTypeAssignment(name="Contains", config={"substring": "x"}, answer_path="$["),
        ]))

    assert exc.value.status_code == 422
    assert exc.value.detail.startswith("'Contains' answer_path is not a valid JSONPath: ")


# -- labels: a type assigned more than once --


def _contains_session():
    session = AsyncMock()
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[_catalogue_row(
        "Contains", [{"key": "substring", "label": "Substring", "kind": "multiline",
                      "required": True}],
    )]))
    return session


def test_validate_passes_the_same_type_twice_with_distinct_labels():
    asyncio.run(_validate_test_type_assignments(_contains_session(), [
        TestTypeAssignment(name="Contains", config={"substring": "refund"}),
        TestTypeAssignment(name="Contains", label="Order number", config={"substring": "4471"}),
    ]))  # no raise: the unlabelled one gets "Contains"


def test_validate_raises_for_duplicate_labels_ignoring_case_and_lists_each_once():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(_contains_session(), [
            TestTypeAssignment(name="Contains", label="Refund", config={"substring": "a"}),
            TestTypeAssignment(name="Contains", label="refund", config={"substring": "b"}),
            TestTypeAssignment(name="Contains", label="REFUND", config={"substring": "c"}),
        ]))

    assert exc.value.status_code == 422
    assert exc.value.detail == "Duplicate labels (letter case aside): ['Refund']"


def test_create_saves_a_repeated_type_as_separate_labelled_assignments():
    session = _contains_session()
    request = CreateTestCaseRequest(name="t", input="q", test_type_assignments=[
        TestTypeAssignment(name="Contains", config={"substring": "refund"}),
        TestTypeAssignment(name="Contains", config={"substring": "4471"}),
    ])

    response = asyncio.run(create_new_test(request, session))

    saved = session.add_all.call_args.args[0]
    assert [(a.test_type_name, a.label, a.config) for a in saved] == [
        ("Contains", "Contains", {"substring": "refund"}),
        ("Contains", "Contains 2", {"substring": "4471"}),
    ]
    assert [a.label for a in response.test_type_assignments] == ["Contains", "Contains 2"]


# -- a test named after its prompt


def test_a_short_prompt_is_the_name_with_its_spaces_made_one():
    assert _name_from_prompt("  How do I\n\n reset   my password? ") == (
        "How do I reset my password?")


def test_a_long_prompt_is_cut_after_its_last_whole_word_within_59_and_marked():
    name = _name_from_prompt("word " * 20)

    assert name == "word " * 11 + "word…"
    assert len(name) == 60


def test_a_long_word_is_cut_at_59():
    assert _name_from_prompt("x" * 80) == "x" * 59 + "…"


def test_a_word_running_past_59_is_left_out_whole():
    assert _name_from_prompt("a" * 58 + " bcdefgh") == "a" * 58 + "…"
