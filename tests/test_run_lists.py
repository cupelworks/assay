# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The run and execution lists on a real database: what each item says without
opening a run, every filter of `GET /runs` and its facets, the executions'
runs counted, and one execution read whole."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from assay.models import TestRunModel, TestStatus

GOOD = [{"name": "Contains", "label": "Says answer", "config": {"substring": "answer"}}]
BAD = [{"name": "Contains", "label": "Says refund", "config": {"substring": "refund"}},
       {"name": "Does Not Contain", "label": "No apology", "config": {"substring": "sorry"}}]


def _ok(response) -> dict:
    assert response.status_code in (200, 201, 202), response.text
    return response.json()


def _not_ran(db, run_id, error) -> None:
    with db.worker_session() as session:
        session.execute(update(TestRunModel).where(TestRunModel.id == run_id)
                        .values(status=TestStatus.not_ran, error=error, results=None))
        session.commit()


@pytest.fixture
def seeded(db):
    """Alpha (one check, met) and Beta (two checks, one not met), a set of
    both, a plan of the set; a standalone run of Alpha, one live run of the
    set and one of the plan, executed in-process; the plan's Alpha run then
    marked Not Ran."""
    alpha = db.test(name="Alpha", checks=GOOD)
    beta = db.test(name="Beta", checks=BAD)
    test_set = db.test_set("Support answers", [alpha, beta])
    plan = db.test_plan("Release check", [test_set])
    standalone = _ok(db.client.post(f"/runs/standalone/{alpha['id']}"))
    set_run = _ok(db.client.post(f"/runs/test-sets/{test_set['id']}"))
    plan_run = _ok(db.client.post(f"/runs/test-plans/{plan['id']}"))
    db.execute(db.dispatched)
    runs = {(r["test_name"], r["origin"]): r
            for r in _ok(db.client.get("/runs"))["items"]}
    _not_ran(db, uuid.UUID(runs[("Alpha", "TestPlan")]["id"]), "The application didn't answer")
    return {"alpha": alpha, "beta": beta, "set": test_set, "plan": plan,
            "standalone": standalone, "set_run": set_run, "plan_run": plan_run, "runs": runs}


def _list(db, **params) -> dict:
    return _ok(db.client.get("/runs", params=params))


def _names(page) -> list[tuple]:
    return sorted((r["test_name"], r["origin"]) for r in page["items"])


# --- GET /runs: what each run says ---


def test_each_run_says_its_test_its_scope_its_checks_and_its_error(db, seeded):
    runs = {(r["test_name"], r["origin"]): r for r in _list(db)["items"]}

    alone = runs[("Alpha", "Standalone")]
    assert (alone["status"], alone["scope_name"], alone["error"]) == ("Green", None, None)
    assert alone["checks"] == {"met": 1, "total": 1, "not_met": []}
    beta = runs[("Beta", "TestSet")]
    assert (beta["status"], beta["scope_name"]) == ("Amber", "Support answers")
    assert beta["checks"] == {"met": 1, "total": 2, "not_met": ["Says refund"]}
    not_ran = runs[("Alpha", "TestPlan")]
    assert (not_ran["status"], not_ran["scope_name"]) == ("NotRan", "Release check")
    assert (not_ran["checks"], not_ran["error"]) == (None, "The application didn't answer")


def test_every_run_names_the_test_it_asked(db, seeded):
    runs = {(r["test_name"], r["origin"]): r for r in _list(db)["items"]}

    assert {runs[("Alpha", origin)]["test_case_id"]["id"]
            for origin in ("Standalone", "TestSet", "TestPlan")} == {seeded["alpha"]["id"]}
    assert runs[("Beta", "TestSet")]["test_case_id"]["id"] == seeded["beta"]["id"]


# --- GET /runs: filters ---


def test_a_filter_given_twice_means_either_value(db, seeded):
    page = _list(db, status=["NotRan", "Amber"])

    assert page["total"] == 3
    assert _names(page) == [("Alpha", "TestPlan"), ("Beta", "TestPlan"), ("Beta", "TestSet")]


def test_different_filters_narrow_together(db, seeded):
    assert _names(_list(db, origin=["TestSet", "TestPlan"], status=["Green"])) == [
        ("Alpha", "TestSet")]
    assert _list(db, origin=["Standalone"])["total"] == 1


def test_runs_of_a_set_or_a_plan(db, seeded):
    assert _names(_list(db, test_set_id=seeded["set"]["id"])) == [
        ("Alpha", "TestSet"), ("Beta", "TestSet")]
    assert _names(_list(db, test_plan_id=seeded["plan"]["id"])) == [
        ("Alpha", "TestPlan"), ("Beta", "TestPlan")]


def test_runs_asking_a_check_type(db, seeded):
    assert _names(_list(db, check_type=["Does Not Contain"])) == [
        ("Beta", "TestPlan"), ("Beta", "TestSet")]
    assert _list(db, check_type=["Toxicity"])["total"] == 0


def test_runs_created_within_a_range(db, seeded):
    now = datetime.now().astimezone()

    assert _list(db, created_from=(now - timedelta(minutes=5)).isoformat())["total"] == 5
    assert _list(db, created_from=(now + timedelta(minutes=5)).isoformat())["total"] == 0
    assert _list(db, created_to=(now - timedelta(minutes=5)).isoformat())["total"] == 0


def test_q_searches_the_test_and_scope_names_ignoring_case(db, seeded):
    assert _names(_list(db, q="bet")) == [("Beta", "TestPlan"), ("Beta", "TestSet")]
    assert _list(db, q="RELEASE")["total"] == 2
    assert _list(db, q="100%")["total"] == 0  # % is matched as itself


def test_each_phrase_of_q_must_be_found_in_any_order(db, seeded):
    assert _names(_list(db, q=["release", "ALP"])) == [("Alpha", "TestPlan")]
    assert _list(db, q=["alpha", "nowhere"])["total"] == 0
    assert _list(db, q=["  ", "beta"])["total"] == 2  # a blank phrase is ignored


def test_newest_or_oldest_first(db, seeded):
    newest = [r["id"] for r in _list(db)["items"]]
    oldest = [r["id"] for r in _list(db, sort="oldest")["items"]]

    assert oldest == newest[::-1]
    assert newest[-1] == seeded["standalone"]["id"]


def test_total_counts_every_run_within_the_filters_whatever_the_page(db, seeded):
    page = _list(db, limit=2, offset=1, origin=["TestSet", "TestPlan"])

    assert (page["total"], len(page["items"])) == (4, 2)


# --- GET /runs/facets ---


def test_facets_count_by_status_and_by_origin_each_within_the_other_filters(db, seeded):
    facets = _ok(db.client.get("/runs/facets", params={"status": ["Green"]}))

    # the status facet ignores the status filter, the origin facet applies it
    assert facets["status"] == {"Pending": 0, "Running": 0, "Green": 2, "Amber": 2, "Red": 0,
                                "NotRan": 1}
    assert facets["origin"] == {"Standalone": 1, "TestSet": 1, "TestPlan": 0}


# --- executions: counted and named ---


def test_every_execution_counts_its_runs_by_status(db, seeded):
    executions = _ok(db.client.get(f"/runs/test-sets/{seeded['set']['id']}/executions"))

    (only,) = executions["items"]
    assert only["runs"] == {"Pending": 0, "Running": 0, "Green": 1, "Amber": 1, "Red": 0,
                            "NotRan": 0}


def test_the_cross_scope_list_names_each_set_or_plan(db, seeded):
    executions = _ok(db.client.get("/runs/executions"))["items"]

    assert sorted((e["origin"], e["name"], e["runs"]["NotRan"]) for e in executions) == [
        ("TestPlan", "Release check", 1), ("TestSet", "Support answers", 0)]


# --- one execution, and its runs ---


def _execution(db, seeded, path: str = "") -> dict:
    plan, execution = seeded["plan"]["id"], seeded["plan_run"]["id"]
    return _ok(db.client.get(f"/runs/test-plans/{plan}/executions/{execution}{path}"))


def test_an_execution_read_whole_gathers_its_checks(db, seeded):
    execution = _execution(db, seeded)

    assert execution["run_count"] == 2
    assert execution["runs"]["NotRan"] == 1 and execution["runs"]["Amber"] == 1
    checks = execution["checks"]
    assert checks["met"] == 1
    assert [(c["test_name"], c["label"]) for c in checks["not_met"]] == [("Beta", "Says refund")]
    assert [(c["test_name"], c["error"], c["checks"]) for c in checks["not_ran"]] == [
        ("Alpha", "The application didn't answer", 1)]


def test_an_execution_of_another_scope_is_a_404(db, seeded):
    response = db.client.get(f"/runs/test-sets/{seeded['set']['id']}/executions/"
                             f"{seeded['plan_run']['id']}")

    assert response.status_code == 404


def test_an_execution_read_is_named_after_its_set_or_plan(db, seeded):
    set_execution = _ok(db.client.get(f"/runs/test-sets/{seeded['set']['id']}/executions"))[
        "items"][0]

    assert _execution(db, seeded)["name"] == "Release check"
    assert _ok(db.client.get(f"/runs/test-sets/{seeded['set']['id']}/executions/"
                             f"{set_execution['id']}"))["name"] == "Support answers"


def test_an_executions_runs_worst_first_or_by_name(db, seeded):
    worst = _execution(db, seeded, "/test-runs?sort=worst_first")["items"]
    by_name = _execution(db, seeded, "/test-runs?sort=name")["items"]

    assert [(r["status"], r["test_name"]) for r in worst] == [("NotRan", "Alpha"),
                                                              ("Amber", "Beta")]
    assert [r["test_name"] for r in by_name] == ["Alpha", "Beta"]
    beta = worst[1]
    assert beta["checks"] == {"met": 1, "total": 2, "not_met": ["Says refund"]}
    assert (beta["output_source"], beta["error"]) == ("recorded", None)


def test_worst_first_orders_by_status_even_against_the_names(db):
    test_set = db.test_set("Order", [db.test(name="Aaa", checks=GOOD),
                                     db.test(name="Zzz", checks=BAD)])
    execution = _ok(db.client.post(f"/runs/test-sets/{test_set['id']}"))
    db.execute(db.dispatched)
    path = f"/runs/test-sets/{test_set['id']}/executions/{execution['id']}/test-runs"

    worst = _ok(db.client.get(path, params={"sort": "worst_first"}))["items"]
    by_name = _ok(db.client.get(path, params={"sort": "name"}))["items"]

    assert [(r["status"], r["test_name"]) for r in worst] == [("Amber", "Zzz"), ("Green", "Aaa")]
    assert [r["test_name"] for r in by_name] == ["Aaa", "Zzz"]
