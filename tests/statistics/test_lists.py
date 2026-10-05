# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The batches and comparisons lists: repeatable filters, the outcome filter
and their facets, on a real database."""
import uuid

import pytest

from tests.facet_checks import facets_agree_with_the_list

CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}


def _batch(db, finish: bool = True, **scope) -> dict:
    response = db.client.post("/statistics/batches",
                              json={"statistical_test": "binomial_gate", **scope})
    assert response.status_code == 202, response.text
    batch = response.json()
    if finish:
        db.execute([run.id for run in db.runs_of(batch["id"])])
    return batch


def _compare(db, a, b) -> dict:
    response = db.client.post("/statistics/comparisons",
                              json={"batch_a": a["id"], "batch_b": b["id"]})
    assert response.status_code == 201, response.text
    return response.json()


def _ids(db, path, **params) -> list[str]:
    response = db.client.get(path, params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


@pytest.fixture
def world(db):
    """"One": a passed batch, then (its answer changed) a failed one, compared
    both ways (worse, then better). "Two": a batch still pending."""
    one, two = db.test(name="One", checks=[CONTAINS]), db.test(name="Two", checks=[CONTAINS])
    passed = _batch(db, test_id=one["id"])
    db.client.patch(f"/tests/{one['id']}", json={"model_output": "nothing useful"})
    failed = _batch(db, test_id=one["id"])
    pending = _batch(db, finish=False, test_id=two["id"])
    worse, better = _compare(db, passed, failed), _compare(db, failed, passed)
    return {"one": one["id"], "two": two["id"], "passed": passed["id"], "failed": failed["id"],
            "pending": pending["id"], "worse": worse["id"], "better": better["id"]}


# --- batches ---


def test_batches_by_any_of_several_statuses_and_scopes(db, world):
    path = "/statistics/batches"

    assert _ids(db, path, status=["Passed", "Failed"]) == [world["failed"], world["passed"]]
    assert _ids(db, path, status="Pending") == [world["pending"]]
    assert len(_ids(db, path, test_id=[world["one"], world["two"]])) == 3
    assert _ids(db, path, test_id=world["one"], status="Pending") == []  # filters narrow
    assert _ids(db, path, test_id=world["one"], test_set_id=str(uuid.uuid4())) == []


def test_batches_counted_by_status_and_scope(db, world):
    facets = db.client.get("/statistics/batches/facets").json()

    assert facets["status"] == {"Pending": 1, "Running": 0, "Passed": 1, "Failed": 1,
                                "Inconclusive": 0, "Incomplete": 0, "NotRan": 0, "Done": 0}
    assert facets["test_id"] == {world["one"]: 2, world["two"]: 1}
    assert facets["test_set_id"] == facets["test_plan_id"] == {}


def test_a_batch_facet_counts_within_the_other_filters_not_its_own(db, world):
    facets = db.client.get("/statistics/batches/facets", params={"status": "Passed"}).json()

    assert facets["status"]["Pending"] == 1  # its own filter isn't applied
    assert facets["test_id"] == {world["one"]: 1}  # the others are
    assert facets_agree_with_the_list(db.client, "/statistics/batches") == 10
    assert facets_agree_with_the_list(db.client, "/statistics/batches", test_id=world["one"]) == 10


# --- comparisons ---


def test_comparisons_by_any_of_several_outcomes(db, world):
    path = "/statistics/comparisons"

    assert _ids(db, path, outcome="worse") == [world["worse"]]
    assert set(_ids(db, path, outcome=["worse", "better"])) == {world["worse"], world["better"]}
    assert _ids(db, path, outcome="no_difference") == []
    assert _ids(db, path, outcome="better", test_id=world["two"]) == []
    assert db.client.get(path, params={"outcome": "great"}).status_code == 422


def test_comparisons_counted_by_outcome_and_scope(db, world):
    facets = db.client.get("/statistics/comparisons/facets").json()

    assert facets["outcome"] == {"better": 1, "worse": 1, "no_difference": 0, "no_worse": 0,
                                 "inconclusive": 0, "none": 0}
    assert facets["test_id"] == {world["one"]: 2}
    assert facets_agree_with_the_list(db.client, "/statistics/comparisons") == 7
    assert facets_agree_with_the_list(db.client, "/statistics/comparisons",
                                      outcome="worse") == 7


def test_the_listed_outcome_is_the_stored_one(db, world):
    listed = db.client.get("/statistics/comparisons").json()["items"]

    assert {item["id"]: item["outcome"] for item in listed} == {world["worse"]: "worse",
                                                                world["better"]: "better"}
