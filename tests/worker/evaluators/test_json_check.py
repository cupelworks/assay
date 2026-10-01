import json

import pytest

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators.engines import json_check

PASS = TestTypeResult(passed=True, score=None, detail=None)
ORDER_SCHEMA = (
    '{"type": "object", "required": ["status"], "properties": {'
    '"status": {"enum": ["approved", "rejected"]}, '
    '"items": {"type": "array", "items": {"type": "object", "required": ["sku"]}}}}'
)


def _evaluate(answer, check="valid", settings=None, **config):
    return json_check.evaluate(EvaluationInput(
        input="q", reference=None, answer=answer, config=config,
        engine_settings={"check": check, **(settings or {})},
    ))


# --- parsing: every check starts here ---


def test_valid_json_passes_with_no_score():
    assert _evaluate('{"status": "approved", "items": []}') == PASS


@pytest.mark.parametrize("answer", ["[1, 2]", '"just a string"', "42", "null"])
def test_any_json_value_is_valid_json(answer):
    assert _evaluate(answer).passed is True


def test_invalid_json_fails_with_the_parsers_reason():
    answer = '{"status": "approved",}'
    # The parser's wording varies by Python version ("Illegal trailing comma"
    # from 3.13, "Expecting property name ..." before), so the expected reason
    # comes from the parser itself.
    with pytest.raises(json.JSONDecodeError) as parser_error:
        json.loads(answer)

    result = _evaluate(answer)

    assert (result.passed, result.score) == (False, None)
    assert result.detail == f"The answer is not valid JSON: {parser_error.value}"


def test_prose_around_json_is_not_json():
    assert _evaluate('Here you go: {"status": "approved"}').passed is False


@pytest.mark.parametrize("answer", [
    '```json\n{"status": "approved"}\n```',
    '```\n{"status": "approved"}\n```',
    '  ```JSON\n{"status": "approved"}```  \n',
])
def test_a_fenced_answer_is_read_from_inside_the_fence_by_default(answer):
    assert _evaluate(answer) == PASS


def test_a_row_can_turn_fence_stripping_off():
    result = _evaluate('```json\n{"a": 1}\n```', settings={"strip_fences": False})

    assert result.passed is False


def test_an_unknown_check_is_a_catalogue_error():
    with pytest.raises(ValueError, match="Unknown JSON check 'yaml'"):
        _evaluate("{}", check="yaml")


# --- schema ---


def test_an_answer_satisfying_the_schema_passes():
    assert _evaluate('{"status": "approved", "items": [{"sku": "A1"}]}', check="schema",
                     schema=ORDER_SCHEMA) == PASS


def test_a_violation_names_where_and_what():
    result = _evaluate('{"status": "pending"}', check="schema", schema=ORDER_SCHEMA)

    assert result == TestTypeResult(
        passed=False, score=None,
        detail="Doesn't match the schema at $.status: 'pending' is not one of "
               "['approved', 'rejected']",
    )


def test_a_violation_at_the_root_names_no_path():
    result = _evaluate("{}", check="schema", schema=ORDER_SCHEMA)

    assert result.detail == "Doesn't match the schema: 'status' is a required property"


def test_invalid_json_fails_before_the_schema_is_consulted():
    result = _evaluate("{oops", check="schema", schema=ORDER_SCHEMA)

    assert result.detail.startswith("The answer is not valid JSON")


@pytest.mark.parametrize("schema,message", [
    ("{oops", "The schema is not valid JSON"),
    ('{"type": "nope"}', "The schema is not a valid JSON Schema"),
    ("", "No schema configured"),
])
def test_a_bad_schema_is_this_types_failure_with_the_reason(schema, message):
    with pytest.raises(ValueError, match=message):
        _evaluate("{}", check="schema", schema=schema)


# --- field ---


def test_the_expected_value_at_the_path_passes():
    assert _evaluate('{"status": "approved"}', check="field", path="$.status",
                     value='"approved"') == PASS


def test_a_different_value_fails_showing_both():
    result = _evaluate('{"status": "pending"}', check="field", path="$.status",
                       value='"approved"')

    assert result == TestTypeResult(passed=False, score=None,
                                    detail='$.status is "pending", expected "approved"')


@pytest.mark.parametrize("actual,expected", [
    ('"42"', "42"),     # a string is not a number
    ("1", "true"),      # a number is not a boolean
    ("0", "false"),
    ("null", '"null"'),
])
def test_values_compare_type_for_type(actual, expected):
    result = _evaluate(f'{{"v": {actual}}}', check="field", path="$.v", value=expected)

    assert result.passed is False


@pytest.mark.parametrize("actual,expected", [
    ("1", "1.0"),                                 # the same JSON number
    ('{"b": 1, "a": [true, null]}', '{"a": [true, null], "b": 1}'),  # key order is irrelevant
])
def test_equal_json_values_pass(actual, expected):
    assert _evaluate(f'{{"v": {actual}}}', check="field", path="$.v",
                     value=expected).passed is True


def test_a_nested_path_and_the_first_of_several_matches():
    answer = '{"items": [{"sku": "A1"}, {"sku": "B2"}]}'

    assert _evaluate(answer, check="field", path="$.items[0].sku", value='"A1"').passed is True
    assert _evaluate(answer, check="field", path="$.items[*].sku", value='"A1"').passed is True


def test_nothing_at_the_path_fails_saying_so():
    result = _evaluate('{"state": "approved"}', check="field", path="$.status",
                       value='"approved"')

    assert result.detail == "Nothing found at $.status"


def test_a_long_value_is_shortened_in_the_detail():
    result = _evaluate(f'{{"v": "{"x" * 200}"}}', check="field", path="$.v", value='"y"')

    assert len(result.detail) < 150
    assert result.detail.endswith('…, expected "y"')


@pytest.mark.parametrize("config,message", [
    ({"path": "$[", "value": "1"}, "JSONPath '\\$\\[' is not valid"),
    ({"path": "$.a", "value": "approved"}, 'write strings in double quotes, e.g. "approved"'),
    ({"value": "1"}, "No JSONPath configured"),
    ({"path": "$.a"}, "No value configured"),
])
def test_a_bad_path_or_value_is_this_types_failure_with_the_reason(config, message):
    with pytest.raises(ValueError, match=message):
        _evaluate('{"a": 1}', check="field", **config)
