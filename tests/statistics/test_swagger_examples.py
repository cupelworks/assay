"""Every example the statistics endpoints show in Swagger is a valid response:
an example that drifted from the schema would teach the FE the wrong shape."""
import pytest

from assay.main import create_app
from assay.schemas.statistics import (
    BatchDetails,
    BatchList,
    ComparisonDetails,
    ComparisonList,
    Estimate,
)

MODELS = {
    ("/statistics/estimate", "post", "200"): Estimate,
    ("/statistics/batches", "post", "202"): BatchDetails,
    ("/statistics/batches", "get", "200"): BatchList,
    ("/statistics/batches/{batch_id}", "get", "200"): BatchDetails,
    ("/statistics/batches/{batch_id}/stop", "post", "200"): BatchDetails,
    ("/statistics/comparisons", "post", "201"): ComparisonDetails,
    ("/statistics/comparisons", "get", "200"): ComparisonList,
    ("/statistics/comparisons/{comparison_id}", "get", "200"): ComparisonDetails,
}


def _examples(content: dict) -> list:
    if "example" in content:
        return [content["example"]]
    return [example["value"] for example in content.get("examples", {}).values()]


@pytest.mark.parametrize("key", list(MODELS), ids=lambda key: f"{key[1]} {key[0]}")
def test_every_success_example_is_a_valid_response(key):
    path, method, code = key
    response = create_app().openapi()["paths"][path][method]["responses"][code]

    examples = _examples(response["content"]["application/json"])

    assert examples
    for example in examples:
        MODELS[key].model_validate(example)
