"""Contains: the answer includes the assignment's `substring`.

Row settings: `case_sensitive`, on by
default, consistent with Exact Match. No trimming — a substring check has
no edge to trim, and a user who wants surrounding whitespace ignored can
leave it out of the substring.

No `score`: a deterministic check is pass/fail by nature, with no scale to
measure on, so `passed` is the whole result.
"""
from assay.schemas import EvaluationInput, TestTypeResult


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    answer = evaluation.answer
    substring = evaluation.config.get("substring")
    if not substring:
        raise ValueError("no substring configured for this test type")

    if not evaluation.engine_settings.get("case_sensitive", True):
        answer, substring = answer.casefold(), substring.casefold()

    if substring in answer:
        return TestTypeResult(passed=True, score=None, detail=None)
    return TestTypeResult(
        passed=False, score=None, detail="required substring not found in the answer",
    )
