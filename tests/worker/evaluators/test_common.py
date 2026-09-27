import pytest

from assay.models import Comparison
from assay.schemas import EvaluationInput
from assay.worker.evaluators._common import parse_threshold, passes, require_reference

# --- require_reference() ---


def test_require_reference_fails_when_the_test_has_no_expected_output():
    assert require_reference(EvaluationInput(input="q", reference="r", answer="a")) == "r"
    with pytest.raises(ValueError, match="no expected output"):
        require_reference(EvaluationInput(input="q", reference=None, answer="a"))


# --- parse_threshold() ---


@pytest.mark.parametrize("raw,expected", [("0.7", 0.7), ("85", 85.0), (" -0.25 ", -0.25)])
def test_parse_threshold_reads_the_string_the_api_stored(raw, expected):
    assert parse_threshold({"threshold": raw}) == expected


@pytest.mark.parametrize("config", [{}, {"threshold": ""}, {"threshold": "   "}])
def test_parse_threshold_missing_or_blank_is_the_users_error(config):
    with pytest.raises(ValueError, match="No threshold configured"):
        parse_threshold(config)


def test_parse_threshold_non_numeric_names_the_value():
    with pytest.raises(ValueError, match="Threshold 'high' is not a number"):
        parse_threshold({"threshold": "high"})


# --- passes() ---


@pytest.mark.parametrize("score,threshold,expected", [
    (0.8, 0.7, True), (0.7, 0.7, True), (0.69, 0.7, False),
])
def test_passes_gte_higher_is_better_with_equality_passing(score, threshold, expected):
    assert passes(score, threshold, Comparison.gte) is expected


@pytest.mark.parametrize("score,threshold,expected", [
    (0.2, 0.3, True), (0.3, 0.3, True), (0.31, 0.3, False),
])
def test_passes_lte_lower_is_better_with_equality_passing(score, threshold, expected):
    assert passes(score, threshold, Comparison.lte) is expected


def test_passes_without_a_comparison_is_a_catalogue_error():
    with pytest.raises(ValueError, match="declares no comparison"):
        passes(0.9, 0.5, None)
