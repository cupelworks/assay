import builtins
import math

import pytest

from assay.models import Comparison
from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators import models
from assay.worker.evaluators.engines import embedding_cosine

REFERENCE = "The refund for order 4471 has been issued and will arrive in five days."
PARAPHRASE = "Your refund for order 4471 was issued; it arrives within five days."


class _Vector(list):
    def __matmul__(self, other):
        return sum(a * b for a, b in zip(self, other, strict=True))


class _FakeModel:
    """Known unit vectors per text, like a sentence-transformers model
    asked for normalized embeddings."""

    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def encode(self, texts, normalize_embeddings, show_progress_bar):
        self.calls.append((texts, normalize_embeddings, show_progress_bar))
        return [_Vector(self.vectors[text]) for text in texts]


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(models, "_models", {})
    monkeypatch.setattr(models, "_loading", {})
    angle = math.radians(60)
    model = _FakeModel({
        REFERENCE: [1.0, 0.0],
        PARAPHRASE: [math.cos(angle), math.sin(angle)],  # cosine 0.5
        "opposite": [-1.0, 0.0],
    })
    loaded = []

    def load(name):
        loaded.append(name)
        return model

    monkeypatch.setattr(embedding_cosine, "_load", load)
    model.loaded = loaded
    return model


def _evaluate(answer, threshold="0.4", settings=None, comparison=Comparison.gte,
              reference=REFERENCE):
    return embedding_cosine.evaluate(EvaluationInput(
        input="q", reference=reference, answer=answer, config={"threshold": threshold},
        engine_settings={"model": "all-MiniLM-L6-v2"} if settings is None else settings,
        comparison=comparison,
    ))


def test_the_score_is_the_cosine_of_the_two_vectors(fake):
    assert _evaluate(PARAPHRASE) == TestTypeResult(passed=True, score=0.5, detail=None)
    assert fake.calls == [([REFERENCE, PARAPHRASE], True, False)]


def test_a_miss_says_by_how_much(fake):
    assert _evaluate(PARAPHRASE, threshold="0.8").detail == "0.5 is below the threshold 0.8"


def test_opposite_meanings_score_minus_1(fake):
    assert _evaluate("opposite").score == -1.0


def test_the_rows_model_is_loaded(fake):
    _evaluate(PARAPHRASE, settings={"model": "paraphrase-multilingual-MiniLM-L12-v2"})

    assert fake.loaded == ["paraphrase-multilingual-MiniLM-L12-v2"]


def test_the_default_model_is_the_english_one(fake):
    _evaluate(PARAPHRASE, settings={})

    assert fake.loaded == ["all-MiniLM-L6-v2"]


@pytest.mark.parametrize("answer", ["", "  \n"])
def test_an_empty_answer_scores_0_without_loading_a_model(fake, answer):
    assert _evaluate(answer).score == 0.0
    assert fake.loaded == []


def test_no_expected_output_is_this_types_failure(fake):
    with pytest.raises(ValueError, match="no expected output"):
        _evaluate(PARAPHRASE, reference=None)


def test_a_worker_without_the_nlp_extra_fails_just_this_check(monkeypatch):
    monkeypatch.setattr(models, "_models", {})
    monkeypatch.setattr(models, "_loading", {})
    real_import = builtins.__import__

    def no_library(name, *args, **kwargs):
        if name.startswith("sentence_transformers"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_library)

    with pytest.raises(ValueError, match="needs the nlp extra"):
        _evaluate(PARAPHRASE)


def test_a_model_that_cannot_load_fails_just_this_check(monkeypatch):
    monkeypatch.setattr(models, "_models", {})
    monkeypatch.setattr(models, "_loading", {})

    def offline(name):
        raise OSError("offline")

    monkeypatch.setattr(embedding_cosine, "_load", offline)

    with pytest.raises(ValueError, match="The model all-MiniLM-L6-v2 couldn't be loaded"):
        _evaluate(PARAPHRASE)
