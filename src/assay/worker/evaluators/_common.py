"""Helpers every scoring engine shares.

Bad config is the user's responsibility: these raise with a message that
says exactly what was wrong, and the per-assignment catch in execute_run
turns that message into the type's own detail. Nothing here sanitizes or falls back to a default.
"""
from assay.models import Comparison
from assay.schemas import EvaluationInput


def require_reference(evaluation: EvaluationInput) -> str:
    """The expected output, for engines that compare against one.

    The API rejects assigning a reference-based type to a test without an
    expected_output, so this only fires for data
    that predates that guard - still reported, not guessed around.
    """
    if evaluation.reference is None:
        raise ValueError("the test has no expected output to compare against")
    return evaluation.reference


def parse_threshold(config: dict[str, str]) -> float:
    """The assignment's threshold as a number.

    Stored as a string like every config value and never validated by the
    API, so this is where a missing or non-numeric
    threshold surfaces — as this type's failure, with the reason.
    """
    raw = config.get("threshold")
    if raw is None or not str(raw).strip():
        raise ValueError("no threshold configured for this test type")
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"threshold {raw!r} is not a number") from None


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
                "this test type's catalogue row declares no comparison (gte/lte), "
                "so its score can't be turned into passed"
            )
