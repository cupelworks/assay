import pytest

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators.engines import contains


def _evaluation(answer, substring="Settings", settings=None):
    config = {"substring": substring} if substring is not None else {}
    return EvaluationInput(
        input="How do I reset?", reference=None, answer=answer, config=config,
        engine_settings={"case_sensitive": True} if settings is None else settings,
    )


def test_present_substring_passes_with_no_score():
    result = contains.evaluate(_evaluation("Go to Settings → Security"))

    assert result == TestTypeResult(passed=True, score=None, detail=None)


def test_absent_substring_fails_with_no_score_and_a_short_reason():
    result = contains.evaluate(_evaluation("Go to Preferences → Security"))

    assert (result.passed, result.score) == (False, None)
    assert result.detail == "required substring not found in the answer"


def test_case_sensitive_by_default_and_a_row_can_turn_it_off():
    assert contains.evaluate(_evaluation("go to settings")).passed is False
    assert contains.evaluate(_evaluation("go to settings",
                                         settings={"case_sensitive": False})).passed is True


def test_no_trimming_whitespace_in_the_substring_is_significant():
    assert contains.evaluate(_evaluation("Go to Settings", substring=" Settings")).passed is True
    assert contains.evaluate(_evaluation("Go toSettings", substring=" Settings")).passed is False


def test_an_empty_answer_is_a_plain_miss_not_an_error():
    assert contains.evaluate(_evaluation("")).passed is False


@pytest.mark.parametrize("substring", [None, ""])
def test_a_missing_or_empty_substring_is_this_types_failure(substring):
    with pytest.raises(ValueError, match="no substring configured"):
        contains.evaluate(_evaluation("anything", substring=substring))
