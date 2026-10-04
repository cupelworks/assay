"""Cosine Similarity and BERTScore on the real models: excluded by default,
run with `pytest -m slow`. Needs the nlp extra, and downloads the models
(about 800 MB) the first time. The BERTScore values are the reference
implementation's (`bert-score`), which the engine reproduces."""
import importlib.util
import threading

import pytest

from assay.models import Comparison
from assay.schemas import EvaluationInput
from assay.worker.evaluators.engines import bertscore, embedding_cosine

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(importlib.util.find_spec("sentence_transformers") is None,
                       reason="the nlp extra isn't installed"),
]

REFERENCE = "The refund for order 4471 has been issued and will arrive in five days."
PARAPHRASE = "Your refund for order 4471 was issued; it arrives within five days."
PADDED = f"{REFERENCE} If it doesn't, contact support and quote your order number."
UNRELATED = "The weather in Rome is sunny today."
ITALIAN = "Il rimborso per l'ordine 4471 è stato emesso e arriverà tra cinque giorni."
CONTRADICTION = "The refund for order 4471 has been rejected and will not arrive."

ENGLISH = {"model": "all-MiniLM-L6-v2"}
MULTILINGUAL = {"model": "paraphrase-multilingual-MiniLM-L12-v2"}
BERTSCORE = {"model": "distilbert-base-uncased", "layer": 5, "measure": "f1",
             "baseline": {"precision": 0.6666033864021301, "recall": 0.6666046380996704,
                          "f1": 0.6662048697471619}}


def _score(engine, answer, settings, reference=REFERENCE):
    return engine.evaluate(EvaluationInput(
        input="q", reference=reference, answer=answer, config={"threshold": "0"},
        engine_settings=settings, comparison=Comparison.gte,
    )).score


@pytest.mark.parametrize("answer,english,multilingual", [
    (REFERENCE, 1.0, 1.0),
    (PARAPHRASE, 0.9676, 0.9265),
    (UNRELATED, 0.0257, -0.0719),
    (ITALIAN, 0.2907, 0.8756),
])
def test_cosine_similarity(answer, english, multilingual):
    assert _score(embedding_cosine, answer, ENGLISH) == english
    assert _score(embedding_cosine, answer, MULTILINGUAL) == multilingual


@pytest.mark.parametrize("answer,f1", [
    (REFERENCE, 1.0),
    (PARAPHRASE, 0.7719),
    (UNRELATED, 0.0357),
    (CONTRADICTION, 0.7754),
    ("Refund issued.", 0.3309),
])
def test_bertscore_rescaled_f1(answer, f1):
    assert _score(bertscore, answer, BERTSCORE) == f1


def test_bertscore_precision_and_recall_tell_padding_apart():
    assert _score(bertscore, PADDED, {**BERTSCORE, "measure": "precision"}) == 0.5669
    assert _score(bertscore, PADDED, {**BERTSCORE, "measure": "recall"}) == 0.955


def test_bertscore_raw_scores_bunch_up():
    assert _score(bertscore, UNRELATED, {**BERTSCORE, "baseline": None}) == 0.6781


def test_both_engines_score_from_many_threads_at_once():
    errors = []

    def score_some(i):
        try:
            for _ in range(10):
                _score(bertscore, f"Refund number {i} was issued", BERTSCORE)
                _score(embedding_cosine, f"Refund {i}", ENGLISH)
        except Exception as exc:  # collected, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=score_some, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
