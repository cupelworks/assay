import pytest

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators.engines import exact_match

DEFAULTS = {"trim": True, "case_sensitive": True}


def _evaluation(answer, reference="Go to Settings → Security", settings=DEFAULTS):
    return EvaluationInput(input="How do I reset?", reference=reference, answer=answer,
                           engine_settings=settings)


def test_a_match_passes_with_score_1_and_no_detail():
    result = exact_match.evaluate(_evaluation("Go to Settings → Security"))

    assert result == TestTypeResult(passed=True, score=1.0, detail=None)


def test_a_mismatch_fails_with_score_0_and_a_short_reason_not_the_texts():
    result = exact_match.evaluate(_evaluation("Go to Settings → Privacy"))

    assert (result.passed, result.score) == (False, 0.0)
    assert result.detail == "differs from the expected output"
    assert "Privacy" not in result.detail


def test_trim_ignores_leading_and_trailing_whitespace_and_newlines_on_both_sides():
    result = exact_match.evaluate(_evaluation("  Go to Settings → Security\n",
                                              reference="\tGo to Settings → Security  "))

    assert result.passed is True


def test_trim_never_touches_inner_whitespace():
    result = exact_match.evaluate(_evaluation("Go to  Settings → Security"))

    assert result.passed is False


def test_trim_off_makes_a_trailing_newline_a_mismatch():
    result = exact_match.evaluate(_evaluation("Go to Settings → Security\n",
                                              settings={"trim": False, "case_sensitive": True}))

    assert result.passed is False


def test_case_sensitive_by_default_and_a_row_can_turn_it_off():
    answer = _evaluation("go to settings → security")

    assert exact_match.evaluate(answer).passed is False
    relaxed = EvaluationInput(**{**answer.model_dump(),
                                 "engine_settings": {"trim": True, "case_sensitive": False}})
    assert exact_match.evaluate(relaxed).passed is True


def test_settings_default_to_trim_and_case_sensitive_when_the_row_omits_them():
    result = exact_match.evaluate(_evaluation("Go to Settings → Security\n", settings={}))

    assert result.passed is True


def test_an_empty_answer_is_a_plain_mismatch_not_an_error():
    result = exact_match.evaluate(_evaluation(""))

    assert (result.passed, result.score) == (False, 0.0)



def test_no_expected_output_is_this_types_failure_with_the_reason():
    with pytest.raises(ValueError, match="no expected output"):
        exact_match.evaluate(_evaluation("anything", reference=None))
