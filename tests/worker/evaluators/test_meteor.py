# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import builtins
import logging
import threading
import time

import pytest

from assay.models import Comparison
from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators.engines import meteor

REFERENCE = "The refund for order 4471 has been issued and will arrive in five days."
PARAPHRASE = "Your refund for order 4471 was issued; it arrives within five days."
DEFAULTS = {"alpha": 0.9, "beta": 3.0, "gamma": 0.5}


def _wordnet_installed() -> bool:
    try:
        meteor._synonym_dictionary()
    except (ImportError, ValueError):
        return False
    return True


# The scoring tests need NLTK's WordNet data, which is downloaded separately
# from the package; everything else runs without it.
needs_wordnet = pytest.mark.skipif(
    not _wordnet_installed(),
    reason="NLTK's WordNet data isn't downloaded: python -m nltk.downloader wordnet",
)


def _evaluate(answer, threshold="0.5", settings=None, comparison=Comparison.gte,
              reference=REFERENCE):
    return meteor.evaluate(EvaluationInput(
        input="q", reference=reference, answer=answer, config={"threshold": threshold},
        engine_settings=DEFAULTS if settings is None else settings, comparison=comparison,
    ))


# --- scoring: known values ---


@needs_wordnet
def test_a_close_paraphrase_scores_its_meteor_and_passes():
    assert _evaluate(PARAPHRASE) == TestTypeResult(passed=True, score=0.5775, detail=None)


@needs_wordnet
def test_an_identical_answer_scores_just_under_1():
    assert _evaluate(REFERENCE).score == 0.9999


@needs_wordnet
def test_capitals_are_ignored():
    assert _evaluate(REFERENCE.upper()).score == 0.9999


@needs_wordnet
def test_other_word_forms_match():
    answer = "The refunds for orders 4471 have been issuing and will be arriving in five days."

    assert _evaluate(answer).score == 0.9226


@needs_wordnet
def test_synonyms_match():
    answer = "The reimbursement for order 4471 has been released and will come in five days."

    assert _evaluate(answer).score == 0.932


@needs_wordnet
def test_synonyms_whose_stems_are_not_words_still_match():
    # stock NLTK looks synonyms up by stem ("larg", "automobil") and finds
    # none: 0.25 on this pair
    result = _evaluate("Please select a big automobile.", reference="Please choose a large car.")

    assert result.score == 0.9977


@needs_wordnet
def test_a_reworded_instruction_scores_high():
    result = _evaluate("To modify your card PIN, open Settings and select Security.",
                       reference="To change your card PIN, go to Settings and choose Security.")

    assert result.score == 0.8441


@needs_wordnet
def test_scrambled_order_costs_a_little():
    answer = "In five days the refund will arrive; it has been issued for order 4471."

    assert _evaluate(answer).score == 0.8907


@needs_wordnet
def test_an_unrelated_answer_scores_low_and_fails():
    assert _evaluate("The weather in Rome is sunny today.") == TestTypeResult(
        passed=False, score=0.1049, detail="0.1049 is below the threshold 0.5",
    )


@needs_wordnet
def test_a_short_answer_covers_little_of_the_expected_text():
    assert _evaluate("Refund issued.").score == 0.1087


@needs_wordnet
def test_an_empty_answer_scores_0():
    assert _evaluate("").score == 0.0


# --- threshold ---


@needs_wordnet
def test_equality_passes():
    assert _evaluate(PARAPHRASE, threshold="0.5775").passed is True


@needs_wordnet
def test_a_miss_says_by_how_much_and_nothing_else():
    assert _evaluate(PARAPHRASE, threshold="0.7").detail == "0.5775 is below the threshold 0.7"


@pytest.mark.parametrize("threshold,message", [
    ("", "No threshold configured"),
    ("high", "Threshold 'high' is not a number"),
])
@needs_wordnet
def test_a_bad_threshold_is_this_types_failure(threshold, message):
    with pytest.raises(ValueError, match=message):
        _evaluate(PARAPHRASE, threshold=threshold)


# --- row settings ---


@needs_wordnet
def test_the_parameters_are_nltks():
    assert _evaluate(PARAPHRASE, settings={**DEFAULTS, "alpha": 0.5}).score == 0.5934
    assert _evaluate(PARAPHRASE, settings={}).score == 0.5775


@needs_wordnet
def test_a_balanced_alpha_lowers_the_score_of_a_padded_answer():
    padded = (f"{REFERENCE} If it doesn't, contact support and quote your order number, and "
              "check your spam folder too.")

    assert _evaluate(padded).score == 0.8491
    assert _evaluate(padded, settings={**DEFAULTS, "alpha": 0.5}).score == 0.5694


def test_a_parameter_that_is_not_a_number_is_a_catalogue_error():
    with pytest.raises(ValueError, match="METEOR setting alpha 'high' is not a number"):
        _evaluate(PARAPHRASE, settings={**DEFAULTS, "alpha": "high"})


# --- words ---


def test_punctuation_is_split_from_words_and_accents_are_kept():
    assert meteor._words("Arriverà in five days.") == ["Arriverà", "in", "five", "days", "."]


# --- inputs and installation ---


def test_no_expected_output_is_this_types_failure():
    with pytest.raises(ValueError, match="no expected output"):
        _evaluate(PARAPHRASE, reference=None)


def _block_nltk(monkeypatch):
    real_import = builtins.__import__

    def no_nltk(name, *args, **kwargs):
        if name.startswith("nltk"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_nltk)


def _no_wordnet(monkeypatch):
    def missing(*_args):
        raise LookupError("Resource wordnet not found")

    monkeypatch.setattr(meteor, "_synonyms", None)
    monkeypatch.setattr(meteor, "_StemmedWordNet", missing)


def test_a_worker_without_the_nlp_extra_fails_just_this_check(monkeypatch):
    _block_nltk(monkeypatch)

    with pytest.raises(ValueError, match="needs the nlp extra"):
        _evaluate(PARAPHRASE)


def test_a_worker_without_wordnet_fails_just_this_check(monkeypatch):
    _no_wordnet(monkeypatch)

    with pytest.raises(ValueError, match="python -m nltk.downloader wordnet"):
        _evaluate(PARAPHRASE)


def test_the_dictionary_is_built_once_however_many_threads_ask(monkeypatch):
    built = []

    def slow_build(*_args):
        time.sleep(0.05)
        built.append(1)
        return object()

    monkeypatch.setattr(meteor, "_synonyms", None)
    monkeypatch.setattr(meteor, "_StemmedWordNet", slow_build)
    threads = [threading.Thread(target=meteor._synonym_dictionary) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(built) == 1


# --- warm-up at worker start ---


@needs_wordnet
def test_warm_up_loads_wordnet(caplog):
    with caplog.at_level(logging.INFO, logger="assay.worker.evaluators.engines.meteor"):
        meteor.warm_up()

    assert "METEOR ready: WordNet loaded" in caplog.text


def test_warm_up_without_wordnet_warns_and_lets_the_worker_start(monkeypatch, caplog):
    _no_wordnet(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="assay.worker.evaluators.engines.meteor"):
        meteor.warm_up()

    assert "METEOR checks will fail on this worker: METEOR's WordNet data isn't installed" \
        in caplog.text


def test_warm_up_without_nltk_does_nothing(monkeypatch, caplog):
    _block_nltk(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="assay.worker.evaluators.engines.meteor"):
        meteor.warm_up()

    assert caplog.text == ""
