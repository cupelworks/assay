# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import pytest

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators.engines import exact_match

DEFAULTS = {"trim": True, "case_sensitive": True}


def _evaluation(answer, reference="Go to Settings → Security", settings=DEFAULTS):
    return EvaluationInput(input="How do I reset?", reference=reference, answer=answer,
                           engine_settings=settings)


def test_a_match_passes_with_no_score_and_no_detail():
    result = exact_match.evaluate(_evaluation("Go to Settings → Security"))

    assert result == TestTypeResult(passed=True, score=None, detail=None)


def test_a_mismatch_fails_with_no_score_and_a_short_reason_not_the_texts():
    result = exact_match.evaluate(_evaluation("Go to Settings → Privacy"))

    assert (result.passed, result.score) == (False, None)
    assert result.detail == "Differs from the expected output"
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

    assert (result.passed, result.score) == (False, None)



def test_no_expected_output_is_this_types_failure_with_the_reason():
    with pytest.raises(ValueError, match="no expected output"):
        exact_match.evaluate(_evaluation("anything", reference=None))


# --- normalize_lookalikes ---


@pytest.mark.parametrize("answer,reference", [
    ("It’s ready", "It's ready"),
    ("Order 4471", "Order 4471"),
    ("Café open", "Café open"),
    ("pass​word", "password"),
    ("It's ready", "It’s ready"),     # whichever side has the look-alike
])
def test_look_alike_characters_match_by_default(answer, reference):
    assert exact_match.evaluate(_evaluation(answer, reference=reference)).passed is True


def test_a_row_can_turn_normalize_lookalikes_off():
    settings = {**DEFAULTS, "normalize_lookalikes": False}

    result = exact_match.evaluate(_evaluation("It’s ready", reference="It's ready",
                                              settings=settings))

    assert result.passed is False


def test_a_zero_width_space_at_the_edge_is_removed_before_trimming():
    assert exact_match.evaluate(_evaluation("​Paris\n", reference="Paris")).passed is True


def test_normalizing_never_hides_a_real_difference():
    assert exact_match.evaluate(_evaluation("It’s not ready",
                                            reference="It's ready")).passed is False
