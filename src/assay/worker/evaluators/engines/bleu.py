# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""BLEU: how much of the answer's wording, in runs of up to four words,
appears in the expected output.

Scored with sacreBLEU (`sacrebleu`, in the `nlp` extra), sentence-level,
with its standard tokenizer. Row settings: `smooth_method` — `exp` (the
default), `floor`, `add-k` or `none`; smoothing gives an answer that shares
no four-word run a small score rather than a flat 0; `lowercase`, off by
default, so capitals count as different words. The score is on BLEU's
native 0–100 scale and passes against the assignment's `threshold` in the
direction the row's `comparison` declares.

An answer shorter than the expected output scores much lower (BLEU's
brevity penalty); a longer one isn't penalised for its length. The
tokenizer keeps accented letters, so any language written with spaces
between words is scored as it is.
"""
from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators._common import against_threshold, require_reference

_SMOOTH_METHODS = ("exp", "floor", "add-k", "none")


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    reference = require_reference(evaluation)
    settings = evaluation.engine_settings
    smooth_method = settings.get("smooth_method", "exp")
    if smooth_method not in _SMOOTH_METHODS:
        raise ValueError(
            f"Unknown BLEU smoothing method {smooth_method!r} on this test type's catalogue row"
        )

    try:
        import sacrebleu
    except ImportError:
        raise ValueError(
            "BLEU isn't installed on this worker: it needs the nlp extra "
            "(the sacrebleu package)"
        ) from None

    score = sacrebleu.sentence_bleu(
        evaluation.answer, [reference],
        smooth_method=smooth_method, lowercase=settings.get("lowercase", False),
    ).score
    return against_threshold(score, evaluation)
