"""Helpers every scoring engine shares.

Bad config is the user's responsibility: these raise with a message that
says exactly what was wrong, and the per-assignment catch in execute_run
turns that message into the type's own detail. Nothing here sanitizes or falls back to a default.
"""
import unicodedata

from assay.models import Comparison
from assay.schemas import EvaluationInput, TestTypeResult

# Characters that look like a plain one, mapped to it. LLMs emit these
# routinely - typographic quotes, a non-breaking space, a non-breaking
# hyphen - and a reader can't tell them from what the expected text has.
_LOOKALIKES = str.maketrans(
    # every space character other than the ordinary one: no-break, narrow
    # no-break, the typesetting widths, ideographic
    dict.fromkeys(
        "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009"
        "\u200a\u202f\u205f\u3000",
        " ",
    )
    # single quotation marks and the modifier-letter apostrophe
    | dict.fromkeys("\u2018\u2019\u201a\u201b\u02bc", "'")
    # double quotation marks
    | dict.fromkeys("\u201c\u201d\u201e\u201f", '"')
    # hyphen, non-breaking hyphen, minus sign
    | dict.fromkeys("\u2010\u2011\u2212", "-")
    # invisible: zero-width space, word joiner, zero-width no-break space
    # (a byte-order mark), soft hyphen
    | dict.fromkeys("\u200b\u2060\ufeff\u00ad", None)
)


def normalize_lookalikes(text: str) -> str:
    """text with look-alike characters made plain, so two texts that read
    the same compare the same.

    Spaces become an ordinary space, typographic quotes straight ones, a
    hyphen or minus look-alike a hyphen-minus, and invisible characters are
    removed. Then accented letters are given one encoding (Unicode NFC), so
    an "é" stored as "e" plus a combining accent equals the single "é".
    Nothing else changes: not letters, not inner whitespace, not dashes
    that look different (en and em dashes), not guillemets.

    NFKC isn't used: it would also rewrite characters that mean something
    else ("m²" into "m2", "½" into "1⁄2").
    """
    # invisible characters go first, so letters they separated can compose
    return unicodedata.normalize("NFC", text.translate(_LOOKALIKES))


def require_reference(evaluation: EvaluationInput) -> str:
    """The expected output, for engines that compare against one.

    The API rejects assigning a reference-based type to a test without an
    expected_output, so this only fires for data
    that predates that guard - still reported, not guessed around.
    """
    if evaluation.reference is None:
        raise ValueError("The test has no expected output to compare against")
    return evaluation.reference


def parse_threshold(config: dict[str, str]) -> float:
    """The assignment's threshold as a number.

    Stored as a string like every config value and never validated by the
    API, so this is where a missing or non-numeric
    threshold surfaces — as this type's failure, with the reason.
    """
    raw = config.get("threshold")
    if raw is None or not str(raw).strip():
        raise ValueError("No threshold configured for this test type")
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Threshold {raw!r} is not a number") from None


def passes(score: float, threshold: float, comparison: Comparison | None) -> bool:
    """Whether score meets threshold in the direction the catalogue row declares.

    Equality passes either way: a score exactly at the threshold is "at least"
    or "at most" it. A row that scores against a threshold but declares no
    comparison is a catalogue error, reported like any other config error.
    """
    match comparison:
        case Comparison.gte:
            return score >= threshold
        case Comparison.lte:
            return score <= threshold
        case _:
            raise ValueError(
                "This test type's catalogue row declares no comparison (gte/lte), "
                "so its score can't be turned into passed"
            )


def against_threshold(score: float, evaluation: EvaluationInput) -> TestTypeResult:
    """The result of a scored type: its score, and whether it meets the
    assignment's threshold in the direction the catalogue row declares.

    The score is kept to four decimals. On a miss, detail says by how much
    ("0.4123 is below the threshold 0.5") and nothing else.

    Raises:
        ValueError: no threshold configured, a non-numeric one, or a row
            with no comparison — this type's failure, with the reason.
    """
    score = round(score, 4)
    threshold = parse_threshold(evaluation.config)
    if passes(score, threshold, evaluation.comparison):
        return TestTypeResult(passed=True, score=score, detail=None)
    side = "below" if evaluation.comparison == Comparison.gte else "above"
    return TestTypeResult(passed=False, score=score,
                          detail=f"{score:g} is {side} the threshold {threshold:g}")
