"""ROUGE: how much of the expected output's wording the answer shares.

Scored with Google's reference implementation (`rouge-score`, in the `nlp`
extra). Row settings: `variant` — `rougeL` (longest common subsequence of
words, the default), `rougeLsum`, or `rouge1` … `rouge9` (shared n-grams);
`measure` — `f1` (default), `precision` (how much of the answer is in the
reference) or `recall` (how much of the reference is in the answer);
`stemmer`, on by default, so "arrive" and "arrives" match. The score is on
ROUGE's native 0–1 scale and passes against the assignment's `threshold`
in the direction the row's `comparison` declares.

On a miss, the detail adds ROUGE-1 and ROUGE-2 F1 for context: how many
single words and word pairs the two texts share.

The library's tokenizer keeps only the letters a–z and digits, lower-cased,
so ROUGE is effectively English-only: "città" becomes "citt".
"""
import re

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators._common import against_threshold, require_reference

_VARIANT = re.compile(r"^rouge(?:[1-9]|L|Lsum)$")
_MEASURES = {"f1": "fmeasure", "precision": "precision", "recall": "recall"}
_CONTEXT_VARIANTS = ("rouge1", "rouge2")


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    reference = require_reference(evaluation)
    settings = evaluation.engine_settings
    variant = settings.get("variant", "rougeL")
    if not _VARIANT.match(variant):
        raise ValueError(f"Unknown ROUGE variant {variant!r} on this test type's catalogue row")
    measure = settings.get("measure", "f1")
    if measure not in _MEASURES:
        raise ValueError(f"Unknown ROUGE measure {measure!r} on this test type's catalogue row")

    try:
        from rouge_score import rouge_scorer
    except ImportError:
        raise ValueError(
            "ROUGE isn't installed on this worker: it needs the nlp extra "
            "(the rouge-score package)"
        ) from None

    variants = [variant, *(v for v in _CONTEXT_VARIANTS if v != variant)]
    scores = rouge_scorer.RougeScorer(
        variants, use_stemmer=settings.get("stemmer", True),
    ).score(reference, evaluation.answer)

    context = ", ".join(
        f"ROUGE-{v.removeprefix('rouge')} F1 {scores[v].fmeasure:.2f}"
        for v in _CONTEXT_VARIANTS if v != variant
    )
    return against_threshold(getattr(scores[variant], _MEASURES[measure]), evaluation,
                             context=context)
