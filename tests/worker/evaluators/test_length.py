import pytest

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators.engines import length

PASS = TestTypeResult(passed=True, score=None, detail=None)


def _evaluate(answer, unit="words", **config):
    return length.evaluate(EvaluationInput(
        input="q", reference=None, answer=answer, config=config,
        engine_settings={"unit": unit},
    ))


# --- words ---


def test_within_the_maximum_passes_with_no_score():
    assert _evaluate("Your refund is on its way.", max="10") == PASS


def test_the_maximum_is_inclusive():
    assert _evaluate("one two three", max="3") == PASS


def test_over_the_maximum_fails_with_the_count_not_the_text():
    result = _evaluate("one two three four", max="3")

    assert result == TestTypeResult(passed=False, score=None,
                                    detail="4 words, the maximum is 3")


def test_under_the_minimum_fails_with_the_count():
    assert _evaluate("Yes.", max="50", min="3").detail == "1 word, the minimum is 3"


def test_the_minimum_is_inclusive_and_optional():
    assert _evaluate("one two three", max="50", min="3") == PASS
    assert _evaluate("one", max="50") == PASS


@pytest.mark.parametrize("answer,words", [
    ("  spaced   out\ttext\n", 3),    # any run of whitespace separates
    ("well-known, isn't it?", 3),     # punctuation stays with its word
    ("", 0),
])
def test_words_are_whitespace_separated(answer, words):
    assert _evaluate(answer, max=str(words)).passed is True
    if words:
        assert _evaluate(answer, max=str(words - 1)).passed is False


# --- characters ---


def test_characters_are_counted_on_the_trimmed_answer():
    assert _evaluate("hi there\n", unit="characters", max="8") == PASS
    assert _evaluate("hi there!", unit="characters", max="8").detail == (
        "9 characters, the maximum is 8")


def test_an_sms_length_limit():
    reply = "x" * 161

    assert _evaluate(reply, unit="characters", max="160").passed is False
    assert _evaluate(reply[:160], unit="characters", max="160") == PASS


# --- bad config is this type's failure, with the reason ---


@pytest.mark.parametrize("config,message", [
    ({}, "No maximum number of words configured"),
    ({"min": "3"}, "No maximum number of words configured"),
    ({"max": "ten"}, "The maximum number of words 'ten' is not a number"),
    ({"max": "2.5"}, "The maximum number of words '2.5' is not a whole number"),
    ({"max": "-1"}, "is not a whole number of 0 or more"),
    ({"max": "3", "min": "x"}, "The minimum number of words 'x' is not a number"),
    ({"max": "3", "min": "5"}, "The minimum \\(5\\) is above the maximum \\(3\\)"),
])
def test_a_bad_bound_raises_with_the_reason(config, message):
    with pytest.raises(ValueError, match=message):
        _evaluate("anything", **config)


def test_a_whole_number_written_with_a_decimal_point_is_accepted():
    assert _evaluate("one two", max="2.0") == PASS


def test_an_unknown_unit_is_a_catalogue_error():
    with pytest.raises(ValueError, match="Unknown length unit 'lines'"):
        _evaluate("x", unit="lines", max="1")
