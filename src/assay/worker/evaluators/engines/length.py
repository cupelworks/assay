"""Length limits: the answer's length is within the assignment's bounds.

Row settings: `unit` is `words` or `characters`. Words are runs of
non-whitespace separated by whitespace (str.split()), so punctuation stays
attached to its word. Characters are counted on the answer with the
whitespace at both ends trimmed — a trailing newline an LLM adds shouldn't
push an answer over a limit — and each Unicode code point counts as one.

Per assignment: `max` is required, `min` is optional; both are whole
numbers, and both bounds are inclusive (a 100-word answer meets "at most
100"). A missing, non-numeric or negative bound, or a `min` above `max`,
raises with the reason — the error is the user's feedback, like an invalid
regex.

No `score`: a limit is met or not, so `passed` is the whole result; the
failure detail gives the count, never the text.
"""
from assay.schemas import EvaluationInput, TestTypeResult

_UNITS = ("words", "characters")


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    unit = evaluation.engine_settings.get("unit", "words")
    if unit not in _UNITS:
        raise ValueError(f"Unknown length unit {unit!r} on this test type's catalogue row")

    maximum = _bound(evaluation.config, "max", unit)
    if maximum is None:
        raise ValueError(f"No maximum number of {unit} configured for this test type")
    minimum = _bound(evaluation.config, "min", unit)
    if minimum is not None and minimum > maximum:
        raise ValueError(f"The minimum ({minimum}) is above the maximum ({maximum})")

    answer = evaluation.answer
    count = len(answer.split()) if unit == "words" else len(answer.strip())

    if count > maximum:
        return _fail(f"{_counted(count, unit)}, the maximum is {maximum}")
    if minimum is not None and count < minimum:
        return _fail(f"{_counted(count, unit)}, the minimum is {minimum}")
    return TestTypeResult(passed=True, score=None, detail=None)


def _bound(config: dict[str, str], key: str, unit: str) -> int | None:
    """The config value as a whole number, None when it isn't set."""
    raw = config.get(key)
    if raw is None or not str(raw).strip():
        return None
    name = "maximum" if key == "max" else "minimum"
    try:
        number = float(raw)
    except ValueError:
        raise ValueError(f"The {name} number of {unit} {raw!r} is not a number") from None
    if not number.is_integer() or number < 0:
        raise ValueError(f"The {name} number of {unit} {raw!r} is not a whole number of 0 or "
                         "more")
    return int(number)


def _counted(count: int, unit: str) -> str:
    noun = unit if count != 1 else unit[:-1]
    return f"{count} {noun}"


def _fail(detail: str) -> TestTypeResult:
    return TestTypeResult(passed=False, score=None, detail=detail)
