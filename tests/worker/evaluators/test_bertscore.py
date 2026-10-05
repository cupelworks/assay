# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import builtins
import importlib.util

import pytest

from assay.models import Comparison
from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators import models
from assay.worker.evaluators.engines import bertscore

REFERENCE = "expected"
ANSWER = "answer"
BASELINE = {"precision": 0.5, "recall": 0.5, "f1": 0.5}

# The scoring arithmetic runs on torch tensors; the rest runs without torch.
needs_torch = pytest.mark.skipif(importlib.util.find_spec("torch") is None,
                                 reason="torch isn't installed (the nlp extra)")


@pytest.fixture
def fake(monkeypatch):
    """Hand-made word-piece vectors: the answer is [CLS] a1 a2 [SEP] and the
    expected output [CLS] e1 [SEP], markers pointing the same way."""
    import torch

    monkeypatch.setattr(models, "_models", {})
    monkeypatch.setattr(models, "_loading", {})
    marker, a1, a2, e1 = [0.0, 1.0], [1.0, 0.0], [0.6, 0.8], [1.0, 0.0]
    vectors = {
        ANSWER: torch.tensor([marker, a1, a2, marker]),
        REFERENCE: torch.tensor([marker, e1, marker]),
    }
    calls = {"loaded": [], "layers": []}

    def load(name):
        calls["loaded"].append(name)
        return "tokenizer", "model"

    def fake_vectors(tokenizer, model, layer, text):
        calls["layers"].append(layer)
        return vectors[text]

    monkeypatch.setattr(bertscore, "_load", load)
    monkeypatch.setattr(bertscore, "_vectors", fake_vectors)
    return calls


def _evaluate(answer=ANSWER, threshold="0.5", settings=None, comparison=Comparison.gte,
              reference=REFERENCE):
    return bertscore.evaluate(EvaluationInput(
        input="q", reference=reference, answer=answer, config={"threshold": threshold},
        engine_settings={"model": "distilbert-base-uncased", "layer": 5, "measure": "f1",
                         "baseline": None} if settings is None else settings,
        comparison=comparison,
    ))


# --- scoring ---


@needs_torch
def test_precision_matches_each_answer_piece_to_its_closest_even_a_marker(fake):
    # a1 matches e1 (1.0); a2's closest is a marker (0.8), not e1 (0.6)
    result = _evaluate(settings={"measure": "precision", "baseline": None})

    assert result.score == 0.9


@needs_torch
def test_recall_matches_each_expected_piece_to_its_closest(fake):
    assert _evaluate(settings={"measure": "recall", "baseline": None}).score == 1.0


@needs_torch
def test_f1_combines_them(fake):
    assert _evaluate() == TestTypeResult(passed=True, score=0.9474, detail=None)


@needs_torch
def test_a_baseline_rescales_the_score(fake):
    result = _evaluate(threshold="0.9", settings={"measure": "f1", "baseline": BASELINE})

    assert result == TestTypeResult(passed=False, score=0.8947,
                                    detail="0.8947 is below the threshold 0.9")


@needs_torch
def test_the_rows_model_and_layer_are_used(fake):
    _evaluate(settings={"model": "roberta-large", "layer": 17, "measure": "f1"})

    assert fake["loaded"] == ["roberta-large"]
    assert fake["layers"] == [17, 17]


@needs_torch
def test_the_defaults_are_distilbert_layer_5_and_f1_without_rescaling(fake):
    assert _evaluate(settings={}).score == 0.9474
    assert fake["loaded"] == ["distilbert-base-uncased"]
    assert fake["layers"] == [5, 5]


# --- inputs and settings ---


@pytest.mark.parametrize("answer", ["", "   "])
def test_an_empty_answer_scores_0_without_loading_a_model(monkeypatch, answer):
    loaded = []
    monkeypatch.setattr(bertscore, "_load", lambda name: loaded.append(name))

    assert _evaluate(answer).score == 0.0
    assert loaded == []


def test_an_unknown_measure_is_a_catalogue_error():
    with pytest.raises(ValueError, match="Unknown BERTScore measure 'accuracy'"):
        _evaluate(settings={"measure": "accuracy"})


def test_a_baseline_without_the_measure_is_a_catalogue_error():
    with pytest.raises(ValueError, match="The BERTScore baseline has no number for recall"):
        _evaluate(settings={"measure": "recall", "baseline": {"f1": 0.66}})


def test_no_expected_output_is_this_types_failure():
    with pytest.raises(ValueError, match="no expected output"):
        _evaluate(reference=None)


def test_a_worker_without_the_nlp_extra_fails_just_this_check(monkeypatch):
    monkeypatch.setattr(models, "_models", {})
    monkeypatch.setattr(models, "_loading", {})
    real_import = builtins.__import__

    def no_library(name, *args, **kwargs):
        if name.startswith("transformers"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_library)

    with pytest.raises(ValueError, match="needs the nlp extra"):
        _evaluate()
