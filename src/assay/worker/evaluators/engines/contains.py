# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Contains: the answer includes the assignment's `substring` — or, with
`negate`, doesn't.

Row settings: `case_sensitive`, on by default, consistent with Exact Match;
`negate`, off by default, turns the check into "must not contain" — the
same matching, the outcome flipped — which is how "Does Not Contain" is a
catalogue row rather than another engine; `normalize_lookalikes`, on by
default, makes look-alike characters plain in both the answer and the
substring, as in Exact Match - which also keeps a forbidden phrase from
slipping past Does Not Contain with a non-breaking space in it. No trimming — a substring check
has no edge to trim, and a user who wants surrounding whitespace ignored can
leave it out of the substring.

No `score`: a deterministic check is pass/fail by nature, with no scale to
measure on, so `passed` is the whole result.
"""
from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators._common import normalize_lookalikes


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    answer = evaluation.answer
    substring = evaluation.config.get("substring")
    if not substring:
        raise ValueError("No substring configured for this test type")

    if evaluation.engine_settings.get("normalize_lookalikes", True):
        answer, substring = normalize_lookalikes(answer), normalize_lookalikes(substring)
    if not evaluation.engine_settings.get("case_sensitive", True):
        answer, substring = answer.casefold(), substring.casefold()

    found = substring in answer
    if evaluation.engine_settings.get("negate", False):
        if found:
            return TestTypeResult(
                passed=False, score=None, detail="Forbidden substring found in the answer",
            )
        return TestTypeResult(passed=True, score=None, detail=None)

    if found:
        return TestTypeResult(passed=True, score=None, detail=None)
    return TestTypeResult(
        passed=False, score=None, detail="Required substring not found in the answer",
    )
