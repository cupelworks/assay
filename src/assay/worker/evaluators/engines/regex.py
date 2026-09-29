"""Regex Match: the assignment's `pattern` matches the answer — or, with
`negate`, doesn't.

Row settings: `mode` is `search`
(match anywhere — what people expect; `^…$` in the pattern still gives a
whole-answer match) or `fullmatch`; `timeout_seconds` bounds one match
attempt, so a catastrophically backtracking pattern fails this one type
instead of hanging a worker thread forever — the `regex` library is used
in place of the stdlib `re` precisely because it supports that timeout.
Flags such as case-insensitivity stay in the user's pattern (`(?i)`).
`negate`, off by default, turns the check into "must not match" — the same
matching, the outcome flipped — which is how "Regex Must Not Match" is a
catalogue row rather than another engine. A timeout fails either way: it
says nothing about whether the pattern would have matched.

An invalid pattern raises with the compiler's own message — the error is
the user's feedback; it is never validated at write time.

No `score`: a deterministic check is pass/fail by nature, with no scale to
measure on, so `passed` is the whole result.
"""
import regex

from assay.schemas import EvaluationInput, TestTypeResult

_MODES = {"search": regex.Pattern.search, "fullmatch": regex.Pattern.fullmatch}
# Why a negated check failed: search looks anywhere, fullmatch at the whole answer
_FORBIDDEN = {
    "search": "Forbidden pattern found in the answer",
    "fullmatch": "The whole answer matches the forbidden pattern",
}


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    answer = evaluation.answer
    pattern = evaluation.config.get("pattern")
    if not pattern:
        raise ValueError("No pattern configured for this test type")

    mode = evaluation.engine_settings.get("mode", "search")
    match_with = _MODES.get(mode)
    if match_with is None:
        raise ValueError(f"Unknown regex mode {mode!r} on this test type's catalogue row")
    timeout = float(evaluation.engine_settings.get("timeout_seconds", 1))

    compiled = regex.compile(pattern)  # regex.error on an invalid pattern, message included
    try:
        # noinspection PyCallingNonCallable
        matched = match_with(compiled, answer, timeout=timeout) is not None
    except TimeoutError:
        return TestTypeResult(
            passed=False, score=None,
            detail=f"Pattern took longer than {timeout:g} s to match — "
                   "likely catastrophic backtracking",
        )

    if evaluation.engine_settings.get("negate", False):
        if matched:
            return TestTypeResult(passed=False, score=None, detail=_FORBIDDEN[mode])
        return TestTypeResult(passed=True, score=None, detail=None)

    if matched:
        return TestTypeResult(passed=True, score=None, detail=None)
    return TestTypeResult(passed=False, score=None, detail=f"Pattern did not {mode} the answer")
