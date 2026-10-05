# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""BERTScore: how closely the answer's words match the expected output's in
meaning, word by word.

Each word piece of both texts gets a contextual vector from a layer of a
BERT-style model (`transformers`, in the `nlp` extra, on CPU). Every piece
of the answer is matched to its closest piece of the expected output
(precision) and every piece of the expected output to its closest in the
answer (recall); F1 combines them. It follows the reference
implementation (`bert-score`) and reproduces its scores to within 1e-6:
the `[CLS]`/`[SEP]` markers can be matched but aren't counted, and there's
no idf weighting.

Row settings: `model` (`distilbert-base-uncased` by default), `layer` (the
hidden layer to read — 5 for that model, as the reference implementation
chose), `measure` (`f1`, `precision` or `recall`) and `baseline` — the
reference implementation's rescaling baseline for that model and layer,
`{"precision": …, "recall": …, "f1": …}`, or none for raw scores. Raw
scores bunch up (an unrelated sentence scored 0.68); rescaled, an
unrelated answer is near 0 and an identical one 1, and a very short or
unrelated answer can go slightly below 0. The score passes against the
assignment's `threshold` in the direction the row's `comparison` declares.
Text beyond the model's input length (512 word pieces) is cut off.

An empty answer scores 0: it has no words to match.
"""
from typing import Any

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators import models
from assay.worker.evaluators._common import against_threshold, require_reference

_MEASURES = ("precision", "recall", "f1")
_NOT_INSTALLED = ("BERTScore isn't installed on this worker: it needs the nlp extra "
                  "(the transformers and torch packages)")


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    reference = require_reference(evaluation)
    settings = evaluation.engine_settings
    measure = settings.get("measure", "f1")
    if measure not in _MEASURES:
        raise ValueError(
            f"Unknown BERTScore measure {measure!r} on this test type's catalogue row"
        )
    baseline = settings.get("baseline")
    if baseline is not None and not isinstance(baseline.get(measure), int | float):
        raise ValueError(
            f"The BERTScore baseline has no number for {measure} on this test type's "
            "catalogue row"
        )
    if not evaluation.answer.strip():
        return against_threshold(0.0, evaluation)

    name = settings.get("model", "distilbert-base-uncased")
    try:
        (tokenizer, model), lock = models.get("transformers", name, _load)
    except ImportError:
        raise ValueError(_NOT_INSTALLED) from None
    layer = int(settings.get("layer", 5))
    with lock:
        answer = _vectors(tokenizer, model, layer, evaluation.answer)
        expected = _vectors(tokenizer, model, layer, reference)

    similarity = answer @ expected.T
    scores = {
        # every piece may match a marker; only the pieces between them count
        "precision": similarity[1:-1].max(dim=1).values.mean().item(),
        "recall": similarity[:, 1:-1].max(dim=0).values.mean().item(),
    }
    scores["f1"] = (2 * scores["precision"] * scores["recall"]
                    / (scores["precision"] + scores["recall"]))
    score = scores[measure]
    if baseline is not None:
        score = (score - baseline[measure]) / (1 - baseline[measure])
    return against_threshold(score, evaluation)


def _load(name: str) -> tuple[Any, Any]:
    from transformers import AutoModel, AutoTokenizer

    return AutoTokenizer.from_pretrained(name), AutoModel.from_pretrained(name).eval()


def _vectors(tokenizer: Any, model: Any, layer: int, text: str) -> Any:
    """One unit vector per word piece of `text`, `[CLS]` and `[SEP]` included."""
    import torch

    encoded = tokenizer(text, return_tensors="pt", truncation=True)
    with torch.no_grad():
        hidden = model(**encoded, output_hidden_states=True).hidden_states[layer][0]
    return torch.nn.functional.normalize(hidden, dim=-1)
