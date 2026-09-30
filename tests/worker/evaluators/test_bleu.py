import builtins

import pytest

from assay.models import Comparison
from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators.engines import bleu

REFERENCE = "The refund for order 4471 has been issued and will arrive in five days."
PARAPHRASE = "Your refund for order 4471 was issued; it arrives within five days."
UNRELATED = "The weather in Rome is sunny today."
DEFAULTS = {"smooth_method": "exp", "lowercase": False}


def _evaluate(answer, threshold="20", settings=None, comparison=Comparison.gte,
              reference=REFERENCE):
    return bleu.evaluate(EvaluationInput(
        input="q", reference=reference, answer=answer, config={"threshold": threshold},
        engine_settings=DEFAULTS if settings is None else settings, comparison=comparison,
    ))


# --- scoring: known values from sacreBLEU ---


def test_a_close_paraphrase_scores_its_bleu_and_passes():
    assert _evaluate(PARAPHRASE) == TestTypeResult(passed=True, score=24.7522, detail=None)


def test_an_identical_answer_scores_100():
    assert _evaluate(REFERENCE).score == 100.0


def test_an_unrelated_answer_scores_low_and_fails():
    assert _evaluate(UNRELATED) == TestTypeResult(
        passed=False, score=3.0297, detail="3.0297 is below the threshold 20",
    )


def test_a_short_answer_is_cut_by_the_brevity_penalty():
    assert _evaluate("Refund issued.").score == 0.635


def test_a_longer_answer_is_not_penalised_for_its_length():
    longer = f"{REFERENCE} If it doesn't, contact support and quote your order number."

    assert _evaluate(longer).score > 50


def test_an_empty_answer_scores_0():
    assert _evaluate("").score == 0.0


def test_accented_letters_are_kept():
    result = _evaluate("Il rimborso è stato emesso e arriverà tra cinque giorni.",
                       reference="Il rimborso è stato emesso e arriverà entro cinque giorni.")

    assert result.score == 70.1688


# --- threshold ---


def test_equality_passes():
    assert _evaluate(PARAPHRASE, threshold="24.7522").passed is True


def test_an_lte_row_passes_at_or_below_and_says_above_on_a_miss():
    assert _evaluate(PARAPHRASE, threshold="30", comparison=Comparison.lte).passed is True
    result = _evaluate(PARAPHRASE, threshold="20", comparison=Comparison.lte)
    assert result.detail == "24.7522 is above the threshold 20"


@pytest.mark.parametrize("threshold,message", [
    ("", "No threshold configured"),
    ("high", "Threshold 'high' is not a number"),
])
def test_a_bad_threshold_is_this_types_failure(threshold, message):
    with pytest.raises(ValueError, match=message):
        _evaluate(PARAPHRASE, threshold=threshold)


# --- row settings ---


def test_capitals_count_as_different_words_by_default():
    assert _evaluate(REFERENCE.upper()).score == 3.1252


def test_lowercase_ignores_capitals():
    settings = {**DEFAULTS, "lowercase": True}

    assert _evaluate(REFERENCE.upper(), settings=settings).score == 100.0


def test_without_smoothing_an_answer_with_no_shared_four_word_run_scores_0():
    settings = {**DEFAULTS, "smooth_method": "none"}

    assert _evaluate(UNRELATED, settings=settings).score == 0.0
    assert _evaluate(PARAPHRASE, settings=settings).score == 24.7522


def test_an_unknown_smoothing_method_is_a_catalogue_error():
    with pytest.raises(ValueError, match="Unknown BLEU smoothing method 'magic'"):
        _evaluate(PARAPHRASE, settings={**DEFAULTS, "smooth_method": "magic"})


def test_missing_settings_fall_back_to_the_defaults():
    assert _evaluate(PARAPHRASE, settings={}).score == 24.7522


# --- inputs and installation ---


def test_no_expected_output_is_this_types_failure():
    with pytest.raises(ValueError, match="no expected output"):
        _evaluate(PARAPHRASE, reference=None)


def test_a_worker_without_the_nlp_extra_fails_just_this_check(monkeypatch):
    real_import = builtins.__import__

    def no_sacrebleu(name, *args, **kwargs):
        if name.startswith("sacrebleu"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sacrebleu)

    with pytest.raises(ValueError, match="needs the nlp extra"):
        _evaluate(PARAPHRASE)
