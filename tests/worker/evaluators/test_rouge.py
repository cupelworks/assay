import builtins

import pytest

from assay.models import Comparison
from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators.engines import rouge

REFERENCE = "The refund for order 4471 has been issued and will arrive in five days."
PARAPHRASE = "Your refund for order 4471 was issued; it arrives within five days."
DEFAULTS = {"variant": "rougeL", "measure": "f1", "stemmer": True}


def _evaluate(answer, threshold="0.5", settings=None, comparison=Comparison.gte,
              reference=REFERENCE):
    return rouge.evaluate(EvaluationInput(
        input="q", reference=reference, answer=answer, config={"threshold": threshold},
        engine_settings=DEFAULTS if settings is None else settings, comparison=comparison,
    ))


# --- scoring: known values from the reference implementation ---


def test_a_close_paraphrase_scores_its_rouge_l_f1_and_passes():
    assert _evaluate(PARAPHRASE) == TestTypeResult(passed=True, score=0.6154, detail=None)


def test_an_identical_answer_scores_1():
    assert _evaluate(REFERENCE).score == 1.0


def test_an_unrelated_answer_scores_low_and_fails():
    result = _evaluate("The weather in Rome is sunny today.")

    assert result.passed is False
    assert result.detail == f"{result.score:g} is below the threshold 0.5"


def test_an_empty_answer_scores_0():
    assert _evaluate("").score == 0.0


# --- threshold ---


def test_a_miss_says_by_how_much_and_nothing_else():
    result = _evaluate(PARAPHRASE, threshold="0.7")

    assert result == TestTypeResult(
        passed=False, score=0.6154, detail="0.6154 is below the threshold 0.7",
    )


def test_equality_passes():
    assert _evaluate(PARAPHRASE, threshold="0.6154").passed is True


def test_an_lte_row_passes_at_or_below_and_says_above_on_a_miss():
    assert _evaluate(PARAPHRASE, threshold="0.7", comparison=Comparison.lte).passed is True
    result = _evaluate(PARAPHRASE, threshold="0.5", comparison=Comparison.lte)
    assert result.detail == "0.6154 is above the threshold 0.5"


@pytest.mark.parametrize("threshold,message", [
    ("", "No threshold configured"),
    ("high", "Threshold 'high' is not a number"),
])
def test_a_bad_threshold_is_this_types_failure(threshold, message):
    with pytest.raises(ValueError, match=message):
        _evaluate(PARAPHRASE, threshold=threshold)


def test_a_row_with_no_comparison_is_a_catalogue_error():
    with pytest.raises(ValueError, match="declares no comparison"):
        _evaluate(PARAPHRASE, comparison=None)


# --- row settings ---


def test_the_stemmer_matches_word_forms():
    without = _evaluate(PARAPHRASE, settings={**DEFAULTS, "stemmer": False}).score

    assert without == 0.5385
    assert without < _evaluate(PARAPHRASE).score


def test_another_variant_scores_by_it():
    result = _evaluate(PARAPHRASE, settings={**DEFAULTS, "variant": "rouge2"})

    assert result.score == 0.3333
    assert result.detail == "0.3333 is below the threshold 0.5"


@pytest.mark.parametrize("measure,score", [("precision", 0.6667), ("recall", 0.5714)])
def test_precision_and_recall(measure, score):
    assert _evaluate(PARAPHRASE, settings={**DEFAULTS, "measure": measure}).score == score


@pytest.mark.parametrize("settings,message", [
    ({**DEFAULTS, "variant": "rougeX"}, "Unknown ROUGE variant 'rougeX'"),
    ({**DEFAULTS, "measure": "accuracy"}, "Unknown ROUGE measure 'accuracy'"),
])
def test_an_unknown_setting_is_a_catalogue_error(settings, message):
    with pytest.raises(ValueError, match=message):
        _evaluate(PARAPHRASE, settings=settings)


# --- inputs and installation ---


def test_no_expected_output_is_this_types_failure():
    with pytest.raises(ValueError, match="no expected output"):
        _evaluate(PARAPHRASE, reference=None)


def test_only_letters_a_to_z_count_so_accented_words_lose_letters():
    # the library's tokenizer drops "à": ROUGE is effectively English-only
    result = _evaluate("citta di Roma", reference="città di Roma", threshold="0")

    assert result.score == 0.6667


def test_a_worker_without_the_nlp_extra_fails_just_this_check(monkeypatch):
    real_import = builtins.__import__

    def no_rouge(name, *args, **kwargs):
        if name.startswith("rouge_score"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_rouge)

    with pytest.raises(ValueError, match="needs the nlp extra"):
        _evaluate(PARAPHRASE)
