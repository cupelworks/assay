from unittest.mock import MagicMock, patch

import pytest

from assay.worker.evaluators.dispatch import evaluate

_PATCH_DETERMINISTIC = "assay.worker.evaluators.dispatch.deterministic.evaluate"
_PATCH_NLP_METRIC = "assay.worker.evaluators.dispatch.nlp_metric.evaluate"
_PATCH_LLM_AS_JUDGE = "assay.worker.evaluators.dispatch.llm_as_judge.evaluate"


@pytest.mark.parametrize(
    "category,patch_target",
    [
        ("deterministic", _PATCH_DETERMINISTIC),
        ("nlp_metric", _PATCH_NLP_METRIC),
        ("llm_as_judge", _PATCH_LLM_AS_JUDGE),
    ],
)
def test_dispatches_to_the_matching_category_module(category, patch_target):
    assignment = MagicMock()
    entry = MagicMock()
    sentinel = MagicMock()

    with patch(patch_target, return_value=sentinel) as mock_evaluate:
        result = evaluate(assignment, category, entry)

    mock_evaluate.assert_called_once_with(assignment, entry)
    assert result is sentinel


def test_unknown_category_raises():
    with pytest.raises(ValueError, match="Unknown test type category: 'bogus'"):
        evaluate(MagicMock(), "bogus", MagicMock())
