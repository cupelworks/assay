# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
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


# --- the examples are real responses, not only valid ones ---


def _without_ids(estimate: dict) -> dict:
    """An estimate with every id blanked: the scope and entries the example
    names don't exist, everything else must match."""
    data = {**estimate, "scope": {**estimate["scope"], "id": None}}
    data["entries"] = [{**entry, "entry_id": None, "test_id": None, "test_set_id": None}
                       for entry in estimate["entries"]]
    return data


def test_the_estimate_examples_are_what_the_estimate_returns(db):
    from assay.api.statistics import _ESTIMATE_GATE, _ESTIMATE_T

    db.client.patch("/settings/target", json={"url": "http://app.test/chat"})
    db.client.patch("/settings/judge", json={"provider": "anthropic", "model": "m"})
    contains = {"name": "Contains", "config": {"substring": "Settings"}}
    support = db.test_set("Support answers", [
        db.test(name="Reset a password", model_output=None,
                checks=[contains, {"name": "Relevance"}]),
        db.test(name="Opening hours", model_output=None, checks=[contains]),
    ])
    gate = db.client.post("/statistics/estimate", json={
        "test_set_id": support["id"], "statistical_test": "binomial_gate"}).json()

    assert _without_ids(gate) == _without_ids(_ESTIMATE_GATE)

    summary = db.test(name="Summarise the outage report", checks=[
        {"name": "ROUGE", "config": {"threshold": "0.5"}},
        {"name": "Word Count Limit", "config": {"max": "120"}}])
    t_test = db.client.post("/statistics/estimate", json={
        "test_id": summary["id"], "statistical_test": "one_sample_t"}).json()

    assert _without_ids(t_test) == _without_ids(_ESTIMATE_T)


def _all_examples(path: str, method: str, code: str) -> list[dict]:
    response = create_app().openapi()["paths"][path][method]["responses"][code]
    return _examples(response["content"]["application/json"])


def test_every_example_names_the_engine_of_its_test():
    for path, method, code in MODELS:
        for example in _all_examples(path, method, code):
            for item in example.get("items", [example]):
                if "engine" in item:
                    assert item["engine"] == item["statistical_test"], (path, item["engine"])


def test_comparison_examples_count_every_verdict_like_real_responses():
    keys = {"better", "worse", "no_difference", "no_worse", "inconclusive", "none"}
    for example in _all_examples("/statistics/comparisons", "post", "201"):
        assert set(example["result"]["verdicts"]) == keys
        assert "paired" in example["result"]


def test_a_stopped_example_carries_its_partial_result():
    examples = {e["status"]: e for e in _all_examples(
        "/statistics/batches/{batch_id}/stop", "post", "200")}

    stopped = examples["Incomplete"]
    assert stopped["result"] is not None
    assert stopped["result"]["summary"] == stopped["summary"]
    running = examples["Running"]
    assert running["result"] is None
    assert all(point["status"] != "Pending"
               for entry in running["progress"]["entries"] for point in entry["strip"])
