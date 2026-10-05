# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Search, filters and sort on the tests, test sets, test plans and datasets
lists and a dataset's rows, on a real database."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from assay.models import TestRunModel, TestStatus
from tests.facet_checks import facets_agree_with_the_list

GOOD = [{"name": "Contains", "label": "Says answer", "config": {"substring": "answer"}}]
BAD = [{"name": "Contains", "label": "Says refund", "config": {"substring": "refund"}}]
TOXIC = [{"name": "Does Not Contain", "label": "No apology", "config": {"substring": "sorry"}}]


def _ok(response) -> dict:
    assert response.status_code in (200, 201, 202), response.text
    return response.json()


def _names(db, path, **params) -> list[str]:
    page = _ok(db.client.get(path, params=params))
    items = page.get("test_cases", page.get("items"))
    return [item["name"] for item in items]


def _total(db, path, **params) -> int:
    return _ok(db.client.get(path, params=params))["total"]


@pytest.fixture
def world(db, tmp_path):
    """Tests: Alpha (green, in Good set), Beta (amber: one met of two, in Mixed
    set), Gamma (never run, no recorded answer, in no set), Delta (made from a
    dataset, a passed trial-free gate batch). Sets: Good (green), Mixed
    (amber), Unrun (never run). Plans: Release (Good), Spare (nothing)."""
    path = tmp_path / "rows.jsonl"
    path.write_text("".join(json.dumps({"prompt": p, "expected_output": "answer",
                                        "model_output": "answer"}) + "\n"
                            for p in ("How do I pay a bill?", "Where is my refund?")))
    dataset = _ok(db.client.post("/datasets/path", json={"path": str(path),
                                                          "dataset_name": "Banking"}))
    empty_path = tmp_path / "empty.jsonl"
    empty_path.write_text(json.dumps({"prompt": "unused", "expected_output": "e",
                                      "model_output": "m"}) + "\n")
    _ok(db.client.post("/datasets/path", json={"path": str(empty_path),
                                               "dataset_name": "Spare rows"}))
    made = _ok(db.client.post("/tests/from-dataset", json={
        "id": dataset["dataset"]["id"], "test_type_assignments": GOOD}))["test_cases"]
    delta_id = made[0]["id"]
    db.client.patch(f"/tests/{delta_id}", json={"name": "Delta"})
    db.client.request("DELETE", "/tests", json=[{"id": made[1]["id"]}])
    alpha = db.test(name="Alpha", input="reset my password", checks=GOOD)
    beta = db.test(name="Beta", input="refund window", checks=GOOD + TOXIC + BAD)
    gamma = db.test(name="Gamma", model_output=None, checks=TOXIC)
    good = db.test_set("Good", [alpha])
    mixed = db.test_set("Mixed", [beta])
    unrun = db.test_set("Unrun", [gamma])
    release = db.test_plan("Release", [good])
    spare = db.test_plan("Spare", [unrun])
    _ok(db.client.post(f"/runs/standalone/{alpha['id']}"))
    _ok(db.client.post(f"/runs/standalone/{beta['id']}"))
    _ok(db.client.post(f"/runs/test-sets/{good['id']}"))
    _ok(db.client.post(f"/runs/test-sets/{mixed['id']}"))
    _ok(db.client.post(f"/runs/test-plans/{release['id']}"))
    db.execute(db.dispatched)
    batch = _ok(db.client.post("/statistics/batches", json={
        "test_id": delta_id, "statistical_test": "binomial_gate"}))
    db.execute([run.id for run in db.runs_of(batch["id"])])
    return {"alpha": alpha, "beta": beta, "gamma": gamma, "delta": delta_id, "good": good,
            "mixed": mixed, "unrun": unrun, "release": release, "spare": spare,
            "dataset": dataset["dataset"]}


# --- tests ---


def test_tests_by_their_latest_run_and_verdict(db, world):
    assert _names(db, "/tests", latest_run="Green", sort="name") == ["Alpha"]
    assert _names(db, "/tests", latest_run=["Amber", "never"], sort="name") == [
        "Beta", "Delta", "Gamma"]
    assert _names(db, "/tests", latest_verdict="Passed") == ["Delta"]
    assert _names(db, "/tests", latest_verdict="none", sort="name") == ["Alpha", "Beta", "Gamma"]


def test_tests_by_check_type_recorded_answer_set_and_dataset(db, world):
    assert _names(db, "/tests", check_type="Does Not Contain", sort="name") == ["Beta", "Gamma"]
    assert _names(db, "/tests", has_recorded_answer="false") == ["Gamma"]
    assert _names(db, "/tests", in_test_set="none") == ["Delta"]
    assert _names(db, "/tests", in_test_set=world["good"]["id"]) == ["Alpha"]
    assert _names(db, "/tests", in_test_set=["any", world["good"]["id"]], sort="name") == [
        "Alpha", "Beta", "Gamma"]
    assert _names(db, "/tests", from_dataset=world["dataset"]["id"]) == ["Delta"]


def test_tests_search_name_input_and_checks_every_phrase(db, world):
    assert _names(db, "/tests", q="PASSWORD") == ["Alpha"]
    assert _names(db, "/tests", q="apology", sort="name") == ["Beta", "Gamma"]
    assert _names(db, "/tests", q=["apology", "refund"]) == ["Beta"]
    assert _total(db, "/tests", q=["alpha", "refund"]) == 0


def test_tests_created_within_a_range(db, world):
    later = (datetime.now().astimezone() + timedelta(minutes=5)).isoformat()

    assert _total(db, "/tests", created_from=later) == 0
    assert _total(db, "/tests", created_to=later) == 4


def test_tests_sorted_by_latest_activity_name_or_creation(db, world):
    # Delta's batch is the newest activity; Gamma never ran, so its creation counts
    assert _names(db, "/tests")[0] == "Delta"
    assert _names(db, "/tests", sort="name") == ["Alpha", "Beta", "Delta", "Gamma"]
    assert _names(db, "/tests", sort="created") == ["Gamma", "Beta", "Alpha", "Delta"]


def test_tests_in_the_order_of_the_rows_they_were_made_from(db, world):
    dataset = world["dataset"]["id"]
    rows = _ok(db.client.get(f"/datasets/{dataset}/rows"))["items"]
    _ok(db.client.post("/tests/from-dataset", json={  # row 2 sent first
        "id": dataset, "test_type_assignments": GOOD, "naming": "prompt",
        "row_ids": [rows[1]["id"], rows[0]["id"]]}))

    assert _names(db, "/tests", sort="dataset_row", from_dataset=dataset) == [
        "Delta", "How do I pay a bill?", "Where is my refund?"]
    assert _names(db, "/tests", sort="dataset_row")[-3:] == ["Alpha", "Beta", "Gamma"]


# --- test sets and plans ---


def test_sets_by_their_latest_executions_outcome(db, world):
    assert _names(db, "/test-sets", latest_run="Green") == ["Good"]
    assert _names(db, "/test-sets", latest_run="Amber") == ["Mixed"]
    assert _names(db, "/test-sets", latest_run="never") == ["Unrun"]


def test_a_set_with_a_run_in_flight_is_running(db, world):
    _ok(db.client.post(f"/runs/test-sets/{world['good']['id']}"))  # not executed: Pending

    assert _names(db, "/test-sets", latest_run="Running") == ["Good"]
    assert _names(db, "/test-sets", latest_run="Green") == []


def test_a_not_ran_run_outranks_the_rest(db, world):
    with db.worker_session() as session:
        session.execute(update(TestRunModel).where(
            TestRunModel.test_set_execution_id.is_not(None))
            .values(status=TestStatus.not_ran, results=None))
        session.commit()

    assert _names(db, "/test-sets", latest_run="NotRan", sort="name") == ["Good", "Mixed"]


def test_sets_by_plan_and_by_test_held(db, world):
    assert _names(db, "/test-sets", in_test_plan="any", sort="name") == ["Good", "Unrun"]
    assert _names(db, "/test-sets", in_test_plan="none") == ["Mixed"]
    assert _names(db, "/test-sets", in_test_plan=world["release"]["id"]) == ["Good"]
    assert _names(db, "/test-sets", holds_test=world["beta"]["id"]) == ["Mixed"]
    assert _names(db, "/test-sets", q="mix") == ["Mixed"]


def test_sets_sorted_by_latest_run_with_never_run_last(db, world):
    names = _names(db, "/test-sets")

    assert names[-1] == "Unrun"
    assert set(names[:2]) == {"Good", "Mixed"}


def test_plans_by_latest_run_and_set_held(db, world):
    assert _names(db, "/test-plans", latest_run="never") == ["Spare"]
    assert _names(db, "/test-plans", latest_run="Green") == ["Release"]
    assert _names(db, "/test-plans", holds_test_set=world["unrun"]["id"]) == ["Spare"]
    assert _names(db, "/test-plans", sort="name") == ["Release", "Spare"]


# --- datasets and rows ---


def test_datasets_by_tests_made_rows_name_and_order(db, world):
    assert _names(db, "/datasets", made_into_tests="true") == ["Banking"]
    assert _names(db, "/datasets", made_into_tests="false") == ["Spare rows"]
    assert _names(db, "/datasets", rows="1-10", sort="name") == ["Banking", "Spare rows"]
    assert _names(db, "/datasets", rows=["0", "101+"]) == []
    assert db.client.get("/datasets", params={"rows": "2-5"}).status_code == 422
    assert _names(db, "/datasets", sort="oldest") == ["Banking", "Spare rows"]
    assert _names(db, "/datasets", q="BANK") == ["Banking"]


def test_datasets_searched_by_their_first_prompt_too(db, world):
    assert _names(db, "/datasets", q="pay a BILL") == ["Banking"]
    assert _names(db, "/datasets", q="refund") == []  # Banking's second row, not its first
    assert _names(db, "/datasets", q=["banking", "pay"]) == ["Banking"]


def test_rows_searched_and_paged_without_offset_or_limit(db, world):
    rows = _ok(db.client.get(f"/datasets/{world['dataset']['id']}/rows", params={"q": "refund"}))

    assert [(r["number"], r["row_info"]["prompt"]) for r in rows["items"]] == [
        (2, "Where is my refund?")]
    assert rows["total"] == 1


# --- what isn't a filter ---


def test_an_unknown_filter_value_or_sort_is_a_422(db, world):
    assert db.client.get("/tests", params={"in_test_set": "some"}).status_code == 422
    assert db.client.get("/tests", params={"latest_run": "Blue"}).status_code == 422
    assert db.client.get("/test-sets", params={"sort": "size"}).status_code == 422
    assert db.client.get("/datasets", params={"limit": 501}).status_code == 422


# --- facets: each count is what the list gives when filtered by that value ---

def _facets_agree_with_the_list(db, path, **chosen):
    return facets_agree_with_the_list(db.client, path, **chosen)


def test_every_facet_count_is_the_lists_total_for_that_value(db, world):
    assert _facets_agree_with_the_list(db, "/tests") > 20
    assert _facets_agree_with_the_list(db, "/test-sets") > 10
    assert _facets_agree_with_the_list(db, "/test-plans") > 10
    assert _facets_agree_with_the_list(db, "/datasets") == 6


def test_each_facet_counts_within_the_other_filters_but_not_its_own(db, world):
    _facets_agree_with_the_list(db, "/tests", latest_run="Green")
    _facets_agree_with_the_list(db, "/test-sets", in_test_plan="any")

    facets = _ok(db.client.get("/tests/facets", params={"latest_run": "Green"}))

    assert facets["latest_run"]["Amber"] == 1  # its own filter isn't applied
    assert facets["has_recorded_answer"] == {"true": 1, "false": 0}  # the others are


def test_the_created_facet_counts_by_the_edges_sent(db, world):
    now = datetime.now().astimezone()
    recent, old = (now - timedelta(hours=1)).isoformat(), (now - timedelta(days=30)).isoformat()

    created = _ok(db.client.get("/tests/facets", params={"created_edges": [recent, old]}))[
        "created"]

    assert created == {recent: 4, old: 4, "before": 0}
    assert _ok(db.client.get("/tests/facets"))["created"] is None
    assert db.client.get("/tests/facets", params={"created_edges": "soon"}).status_code == 422
