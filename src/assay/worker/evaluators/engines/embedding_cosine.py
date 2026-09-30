"""Cosine Similarity: how close in meaning the answer is to the expected
output, as a whole, whatever the wording.

Both texts are turned into one vector each by a sentence-embedding model
(`sentence-transformers`, in the `nlp` extra), on CPU; the score is the
cosine of the angle between them, on its native −1 to 1 scale, and passes
against the assignment's `threshold` in the direction the row's
`comparison` declares. Row setting `model`: a sentence-transformers model
name — `all-MiniLM-L6-v2` (English) by default; a multilingual model makes
a row for other languages, or for an answer and an expected output in
different languages. Text beyond the model's input length is cut off.

An empty answer scores 0: it has no meaning to compare.
"""
from typing import Any

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators import models
from assay.worker.evaluators._common import against_threshold, require_reference

_NOT_INSTALLED = ("Cosine Similarity isn't installed on this worker: it needs the nlp extra "
                  "(the sentence-transformers package)")


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    reference = require_reference(evaluation)
    if not evaluation.answer.strip():
        return against_threshold(0.0, evaluation)
    name = evaluation.engine_settings.get("model", "all-MiniLM-L6-v2")
    try:
        model, lock = models.get("sentence-transformers", name, _load)
    except ImportError:
        raise ValueError(_NOT_INSTALLED) from None
    with lock:
        vectors = model.encode([reference, evaluation.answer], normalize_embeddings=True,
                               show_progress_bar=False)
    return against_threshold(float(vectors[0] @ vectors[1]), evaluation)


def _load(name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name, device="cpu")
