"""Exact Match: the answer equals the expected output.

Row settings (docs/evaluators/dev_notes.md note 6): `trim` strips leading
and trailing whitespace — spaces, tabs and newlines, i.e. str.strip() — from
both texts before comparing, because LLM answers routinely end in a stray
newline that shouldn't fail an otherwise exact check; `case_sensitive`
keeps "exact" meaning exact by default. A case-insensitive variant is a
catalogue row with the flag flipped, not a code change.
"""
from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators._common import require_answer, require_reference


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    answer = require_answer(evaluation)
    reference = require_reference(evaluation)

    if evaluation.engine_settings.get("trim", True):
        answer, reference = answer.strip(), reference.strip()
    if not evaluation.engine_settings.get("case_sensitive", True):
        answer, reference = answer.casefold(), reference.casefold()

    if answer == reference:
        return TestTypeResult(passed=True, score=1.0, detail=None)
    # Not the texts themselves - the run detail already shows both.
    return TestTypeResult(passed=False, score=0.0, detail="differs from the expected output")
