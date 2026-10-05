# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""How tests, test sets, test plans, entries, datasets and rows stand, on a real
database: what their lists and pages read, and that the delete guards refuse
exactly what the lists say can't be deleted."""
import json
import uuid

import pytest

GOOD = [{"name": "Contains", "label": "Says answer", "config": {"substring": "answer"}}]


def _ok(response) -> dict:
    assert response.status_code in (200, 201, 202), response.text
    return response.json()


def _by_name(items) -> dict:
    return {item["name"]: item for item in items}


@pytest.fixture
def world(db, tmp_path):
    """A dataset of three rows, tests made from two of them, a test made by
    hand, a set holding two tests (one copy since unlinked), a plan linking
    the set, a standalone run, a set run, a plan run, a batch of the set."""
    path = tmp_path / "d.jsonl"
    path.write_text("".join(json.dumps({"prompt": p, "expected_output": "answer",
                                        "model_output": "answer"}) + "\n"
                            for p in ("first", "second", "third")))
    dataset = _ok(db.client.post("/datasets/path", json={"path": str(path),
                                                          "dataset_name": "Questions"}))
    made = _ok(db.client.post("/tests/from-dataset", json={
        "id": dataset["dataset"]["id"], "test_type_assignments": GOOD}))
    by_hand = db.test(name="By hand", checks=GOOD)
    idle = db.test(name="Idle", checks=GOOD)
    made_ids = [t["id"] for t in made["test_cases"]]
    test_set = db.test_set("Support", [by_hand, {"id": made_ids[0]}])
    entries = _ok(db.client.get(f"/test-sets/{test_set['id']}/entries"))["items"]
    plan = db.test_plan("Release", [test_set])
    _ok(db.client.post(f"/runs/standalone/{by_hand['id']}"))
    _ok(db.client.post(f"/runs/test-sets/{test_set['id']}"))
    _ok(db.client.post(f"/runs/test-plans/{plan['id']}"))
    db.execute(db.dispatched)
    batch = _ok(db.client.post("/statistics/batches", json={
        "test_set_id": test_set["id"], "statistical_test": "binomial_gate"}))
    empty_set = db.test_set("Empty", [])
    idle_plan = db.test_plan("Idle plan", [empty_set])
    return {"dataset": dataset["dataset"], "made": made_ids, "by_hand": by_hand, "idle": idle,
            "set": test_set, "entries": entries, "plan": plan, "batch": batch,
            "empty_set": empty_set, "idle_plan": idle_plan}


# --- tests ---


def test_a_test_says_how_it_stands_and_where_it_came_from(db, world):
    tests = _by_name(_ok(db.client.get("/tests"))["test_cases"])
    by_hand, idle = tests["By hand"], tests["Idle"]

    assert by_hand["latest_run"]["status"] == "Green" and by_hand["latest_batch"] is None
    assert (by_hand["has_runs"], by_hand["copy_count"]) == (True, 1)
    assert (by_hand["dataset_row_id"], by_hand["dataset_row_number"]) == (None, None)
    assert (idle["latest_run"], idle["has_runs"], idle["copy_count"]) == (None, False, 0)
    assert by_hand["created_at"]
    made = [t for t in tests.values() if t["dataset_row_id"]]
    assert sorted(t["dataset_row_number"] for t in made) == [1, 2, 3]


def test_a_tests_page_says_the_same_as_its_list_item(db, world):
    listed = _by_name(_ok(db.client.get("/tests"))["test_cases"])["By hand"]

    assert _ok(db.client.get(f"/tests/{world['by_hand']['id']}")) == listed


def test_a_batch_run_counts_as_a_run_but_not_as_the_latest_run(db, world):
    test = world["idle"]
    _ok(db.client.post("/statistics/batches", json={"test_id": test["id"],
                                                     "statistical_test": "trial"}))

    item = _ok(db.client.get(f"/tests/{test['id']}"))

    assert item["has_runs"] is True
    assert item["latest_run"] is None
    assert item["latest_batch"]["status"] == "Pending"


# --- sets and plans ---


def test_a_set_says_its_latest_batch_its_latest_execution_and_how_often_it_ran(db, world):
    sets = _by_name(_ok(db.client.get("/test-sets"))["items"])
    support, empty = sets["Support"], sets["Empty"]

    assert support["latest_batch"]["id"] == world["batch"]["id"]
    latest = support["latest_execution"]
    assert (latest["replayed"], latest["runs"]["Green"]) == (False, 2)
    assert (support["execution_count"], support["has_runs"]) == (1, True)
    assert (empty["latest_batch"], empty["latest_execution"], empty["execution_count"],
            empty["has_runs"]) == (None, None, 0, False)


def test_a_plan_and_its_linked_sets_say_how_they_stand(db, world):
    plans = _by_name(_ok(db.client.get("/test-plans"))["items"])
    release = plans["Release"]

    assert (release["execution_count"], release["has_runs"]) == (1, True)
    assert release["latest_execution"]["runs"]["Green"] == 2
    assert (plans["Idle plan"]["has_runs"], plans["Idle plan"]["latest_execution"]) == (
        False, None)
    (linked,) = _ok(db.client.get(f"/test-plans/{world['plan']['id']}/entries"))["items"]
    assert linked["test_set"]["execution_count"] == 1
    assert linked["test_set"]["latest_batch"]["id"] == world["batch"]["id"]


def test_set_and_plan_pages_say_the_same_as_their_list_items(db, world):
    sets = _by_name(_ok(db.client.get("/test-sets"))["items"])
    plans = _by_name(_ok(db.client.get("/test-plans"))["items"])

    assert _ok(db.client.get(f"/test-sets/{world['set']['id']}")) == sets["Support"]
    assert _ok(db.client.get(f"/test-plans/{world['plan']['id']}")) == plans["Release"]


# --- entries ---


def test_an_entry_that_ran_is_frozen(db, world):
    test_set = db.test_set("Fresh", [world["idle"]])
    (fresh,) = _ok(db.client.get(f"/test-sets/{test_set['id']}/entries"))["items"]
    ran = _ok(db.client.get(f"/test-sets/{world['set']['id']}/entries"))["items"]

    assert fresh["has_runs"] is False
    assert all(entry["has_runs"] for entry in ran)
    entry = ran[0]
    assert _ok(db.client.get(f"/test-sets/{world['set']['id']}/entries/{entry['id']}")) == entry


# --- datasets and rows ---


def test_a_dataset_counts_its_rows_and_shows_its_first_prompt(db, world):
    item = _by_name(_ok(db.client.get("/datasets"))["items"])["Questions"]

    assert (item["row_count"], item["first_prompt"]) == (3, "first")
    assert _ok(db.client.get(f"/datasets/{world['dataset']['id']}")) == item


def test_each_row_carries_its_number_and_how_many_tests_were_made_from_it(db, world):
    rows = _ok(db.client.get(f"/datasets/{world['dataset']['id']}/rows",
                             params={"offset": 0, "limit": 10}))["items"]

    assert [(r["number"], r["row_info"]["prompt"], r["test_count"]) for r in rows] == [
        (1, "first", 1), (2, "second", 1), (3, "third", 1)]


# --- what the lists say, the delete guards refuse ---


def test_a_test_with_runs_or_copies_cant_be_deleted_and_one_without_can(db, world):
    by_hand, idle = world["by_hand"]["id"], world["idle"]["id"]

    refused = db.client.request("DELETE", "/tests", json=[{"id": by_hand}])
    deleted = db.client.request("DELETE", "/tests", json=[{"id": idle}])

    assert refused.status_code == 409
    assert deleted.status_code in (200, 204), deleted.text


def test_a_set_and_a_plan_that_ran_cant_be_deleted_and_ones_that_didnt_can(db, world):
    assert db.client.delete(f"/test-sets/{world['set']['id']}").status_code == 409
    assert db.client.delete(f"/test-plans/{world['plan']['id']}").status_code == 409
    assert db.client.delete(f"/test-plans/{world['idle_plan']['id']}").status_code in (200, 204)
    unlinked = db.test_set("Unlinked", [])
    assert db.client.delete(f"/test-sets/{unlinked['id']}").status_code in (200, 204)


def test_an_entry_that_ran_cant_be_deleted(db, world):
    entry = world["entries"][0]

    response = db.client.request("DELETE", f"/test-sets/{world['set']['id']}/entries",
                                 json=[{"id": entry["id"]}])

    assert response.status_code == 409
    assert str(uuid.UUID(entry["id"])) in response.text


# --- which sets hold a test, which plans link a set ---


def test_the_sets_holding_a_test_say_whether_each_copy_ran_and_still_matches(db, world):
    by_hand = world["by_hand"]
    fresh_set = db.test_set("Another", [by_hand])
    db.client.patch(f"/tests/{by_hand['id']}", json={"input": "a changed question"})
    (ran_entry,) = [e for e in world["entries"] if e["test_case_id"]["id"] == by_hand["id"]]
    unlinked = db.client.patch(f"/test-sets/{world['set']['id']}/entries",
                               json=[{"id": ran_entry["id"]}])
    assert unlinked.status_code == 200, unlinked.text

    holding = _ok(db.client.get(f"/tests/{by_hand['id']}/test-sets"))

    assert [h["test_set"]["name"] for h in holding["items"]] == ["Another"]
    (another,) = holding["items"]
    assert another["test_set"]["id"] == fresh_set["id"]
    assert (another["entry"]["has_runs"], another["entry"]["matches_test"]) == (False, False)


def test_a_copy_matches_its_test_until_the_test_asks_something_else(db, world):
    test = db.test(name="Checked", checks=GOOD)
    db.test_set("Holder", [test])

    def matches():
        return _ok(db.client.get(f"/tests/{test['id']}/test-sets"))["items"][0]["entry"][
            "matches_test"]

    assert matches() is True
    db.client.patch(f"/tests/{test['id']}", json={"name": "Renamed"})
    assert matches() is True  # the name isn't compared
    db.client.patch(f"/tests/{test['id']}", json={"test_type_assignments": [
        {"name": "Contains", "label": "Says answer", "config": {"substring": "other"}}]})
    assert matches() is False


def test_the_plans_linking_a_set_and_the_counts_on_the_lists(db, world):
    second = db.test_plan("Second plan", [world["set"]])

    links = _ok(db.client.get(f"/test-sets/{world['set']['id']}/test-plans"))

    plans = [link["test_plan"] for link in links["items"]]
    assert [(plan["name"], plan["linked_set_count"]) for plan in plans] == [
        ("Release", 1), ("Second plan", 1)]
    entry_ids = {link["test_plan"]["id"]: link["entry_id"] for link in links["items"]}
    (second_entry,) = _ok(db.client.get(f"/test-plans/{second['id']}/entries"))["items"]
    assert entry_ids[second["id"]] == second_entry["id"]
    sets = _by_name(_ok(db.client.get("/test-sets"))["items"])
    assert (sets["Support"]["test_plan_count"], sets["Empty"]["test_plan_count"]) == (2, 1)
    tests = _by_name(_ok(db.client.get("/tests"))["test_cases"])
    assert (tests["By hand"]["test_set_count"], tests["Idle"]["test_set_count"]) == (1, 0)


# --- making tests from a dataset: which rows, how they're named, their answers ---


def _make(db, dataset_id, **options) -> list[dict]:
    made = _ok(db.client.post("/tests/from-dataset", json={"id": dataset_id, **options}))
    return [_ok(db.client.get(f"/tests/{t['id']}")) for t in made["test_cases"]]


def test_only_the_ticked_rows_named_after_their_prompts_and_asking_live(db, world):
    rows = _ok(db.client.get(f"/datasets/{world['dataset']['id']}/rows"))["items"]

    made = _make(db, world["dataset"]["id"], row_ids=[rows[2]["id"], rows[0]["id"]],
                 naming="prompt", recorded_answers="leave_out", test_type_assignments=GOOD)

    assert [(t["name"], t["dataset_row_number"], t["model_output"]) for t in made] == [
        ("first", 1, None), ("third", 3, None)]


def test_the_defaults_keep_numbered_names_and_the_recorded_answers(db, world):
    made = _make(db, world["dataset"]["id"])

    assert len(made) == 3
    assert all(t["name"].startswith("New Test ") and t["model_output"] == "answer" for t in made)


def test_a_row_of_another_dataset_is_a_422_naming_it(db, world, tmp_path):
    other = tmp_path / "other.jsonl"
    other.write_text(json.dumps({"prompt": "p", "expected_output": "e",
                                 "model_output": "m"}) + "\n")
    stranger = _ok(db.client.post("/datasets/path", json={"path": str(other),
                                                           "dataset_name": "Other"}))
    stranger_row = stranger["loaded"]["ids"][0]

    response = db.client.post("/tests/from-dataset", json={
        "id": world["dataset"]["id"], "row_ids": [stranger_row]})

    assert response.status_code == 422
    assert stranger_row in response.text
