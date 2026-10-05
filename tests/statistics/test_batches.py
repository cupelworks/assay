# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Batches end to end on a real database: creation across the three scopes, the
runs executed in-process by the worker's own service, the result computed on
read and stored, the listing, and the stop."""
import uuid

from assay.models import TestStatus

CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}
MISSES = {"name": "Contains", "label": "Misses", "config": {"substring": "absent"}}
GATE = {"statistical_test": "binomial_gate"}


def _create(db, **body):
    return db.client.post("/statistics/batches", json={**GATE, **body})


def _get(db, batch_id, **params):
    return db.client.get(f"/statistics/batches/{batch_id}", params=params).json()


def _run_all(db, batch_id):
    db.execute([run.id for run in db.runs_of(batch_id)])


# --- creation ---


def test_a_test_batch_creates_n_standalone_runs_and_dispatches_them(db):
    test = db.test()

    response = _create(db, test_id=test["id"], note="  prompt v3  ")

    assert response.status_code == 202
    batch = response.json()
    assert (batch["status"], batch["progress"]["times_requested"]) == ("Pending", 29)
    assert batch["scope"] == {"kind": "test", "id": test["id"], "name": "t"}
    assert batch["note"] == "prompt v3"
    assert batch["parameters"] == {"target": 0.9, "confidence": 0.95}
    assert batch["floor"] == 29 and batch["result"] is None
    runs = db.runs_of(batch["id"])
    assert [run.batch_index for run in runs] == list(range(1, 30))
    assert {str(run.test_id) for run in runs} == {test["id"]}
    assert {run.id for run in runs} == set(db.dispatched)
    # ordinary standalone runs: listed with the test's runs
    assert db.client.get(f"/runs/standalone/{test['id']}/test-runs").json()["total"] == 29


def test_a_set_batch_creates_one_execution_per_time(db):
    test_set = db.test_set("support", [db.test(name="a"), db.test(name="b")])

    batch = _create(db, test_set_id=test_set["id"], times=30).json()

    runs = db.runs_of(batch["id"])
    assert batch["progress"]["runs_total"] == len(runs) == 60
    executions = {run.test_set_execution_id for run in runs}
    assert len(executions) == 30
    for index in (1, 30):
        assert len([r for r in runs if r.batch_index == index]) == 2


def test_a_plan_batch_creates_one_plan_execution_per_time(db):
    plan = db.test_plan("campaign", [db.test_set("a", [db.test(name="one")]),
                                     db.test_set("b", [db.test(name="two")])])

    batch = _create(db, test_plan_id=plan["id"]).json()

    runs = db.runs_of(batch["id"])
    assert len(runs) == 58
    assert len({run.test_plan_execution_id for run in runs}) == 29


def test_creation_has_the_estimates_guards(db):
    test = db.test()

    assert _create(db, test_id=test["id"], times=28).status_code == 422
    assert _create(db, test_id=test["id"], note="x" * 501).status_code == 422
    assert _create(db, test_set_id=db.test_set("empty", [])["id"]).status_code == 409
    assert _create(db, test_id="00000000-0000-0000-0000-000000000000").status_code == 404
    assert db.dispatched == []


# --- reading ---


def test_a_running_batch_shows_progress_and_no_result(db):
    test = db.test()
    batch = _create(db, test_id=test["id"]).json()
    runs = db.runs_of(batch["id"])
    db.execute([runs[0].id, runs[1].id])
    db.set_status(runs[2].id, TestStatus.running)

    read = _get(db, batch["id"])

    assert read["status"] == "Running"
    assert read["result"] is None
    assert read["verdicts"] is None
    progress = read["progress"]
    assert (progress["times_done"], progress["runs_done"]) == (2, 2)
    assert progress["runs"] == {"Pending": 26, "Running": 1, "Green": 2, "Amber": 0, "Red": 0,
                                "NotRan": 0}
    # the runs matrix so far: statuses by time, no verdicts
    [entry] = progress["entries"]
    assert (entry["entry_id"], entry["test_id"], entry["name"]) == (None, test["id"], "t")
    assert entry["runs"] == progress["runs"]
    assert [p["status"] for p in entry["strip"]][:4] == ["Green", "Green", "Running", "Pending"]
    assert [p["index"] for p in entry["strip"]] == list(range(1, 30))
    assert entry["strip"][0]["run_id"] == str(runs[0].id)
    assert entry["strip"][0]["execution_id"] is None


def test_the_live_matrix_is_left_out_of_lists_series_false_and_results(db):
    test_set = db.test_set("support", [db.test(name="b"), db.test(name="a")])
    batch = _create(db, test_set_id=test_set["id"]).json()

    read = _get(db, batch["id"])
    listed = db.client.get("/statistics/batches").json()["items"][0]
    slim = _get(db, batch["id"], series="false")

    assert [(e["test_set_name"], e["name"]) for e in read["progress"]["entries"]] == [
        ("support", "a"), ("support", "b")]
    assert read["progress"]["entries"][0]["strip"][0]["execution_id"] is not None
    assert listed["progress"]["entries"] is None
    assert slim["progress"]["entries"] is None

    _run_all(db, batch["id"])
    finished = _get(db, batch["id"])

    assert finished["progress"]["entries"] is None
    assert len(finished["result"]["entries"][0]["strip"]) == 29


def test_a_finished_batch_is_computed_once_and_stored(db):
    test = db.test(checks=[CONTAINS])
    batch = _create(db, test_id=test["id"]).json()
    _run_all(db, batch["id"])

    first = _get(db, batch["id"])
    second = _get(db, batch["id"])

    assert first["status"] == "Passed"
    assert first["summary"] == "Passed: the check met the goal."
    assert first["verdicts"] == {"pass": 1, "fail": 0, "inconclusive": 0, "none": 0}
    assert first["verdicts"] == first["result"]["verdicts"]
    assert first["completed_at"] is not None
    assert first == second
    check = first["result"]["entries"][0]["checks"][0]
    assert check["statistic"]["verdict"] == "pass"
    assert check["counts"]["evaluated"] == 29
    assert [p["index"] for p in check["series"]] == list(range(1, 30))
    assert len(first["result"]["entries"][0]["strip"]) == 29


def test_series_false_leaves_out_the_points(db):
    test = db.test()
    batch = _create(db, test_id=test["id"]).json()
    _run_all(db, batch["id"])

    read = _get(db, batch["id"], series="false")

    entry = read["result"]["entries"][0]
    assert entry["strip"] is None
    assert entry["checks"][0]["series"] is None
    assert entry["checks"][0]["statistic"]["verdict"] == "pass"


def test_one_proven_failure_fails_the_batch(db):
    test = db.test(checks=[CONTAINS, MISSES])
    batch = _create(db, test_id=test["id"]).json()
    _run_all(db, batch["id"])

    read = _get(db, batch["id"])

    assert read["status"] == "Failed"
    verdicts = {c["label"]: c["statistic"]["verdict"]
                for c in read["result"]["entries"][0]["checks"]}
    assert verdicts == {"Contains": "pass", "Misses": "fail"}


def test_nothing_evaluated_is_not_ran(db):
    test = db.test(model_output=None)
    batch = _create(db, test_id=test["id"]).json()
    _run_all(db, batch["id"])

    read = _get(db, batch["id"])

    assert read["status"] == "NotRan"
    assert read["summary"].startswith("Not Ran: no run could be carried out")


def test_a_set_batchs_entries_and_their_executions(db):
    test_set = db.test_set("support", [db.test(name="b"), db.test(name="a")])
    batch = _create(db, test_set_id=test_set["id"]).json()
    _run_all(db, batch["id"])

    entries = _get(db, batch["id"])["result"]["entries"]

    assert [(e["test_set_name"], e["name"]) for e in entries] == [("support", "a"),
                                                                 ("support", "b")]
    point = entries[0]["checks"][0]["series"][0]
    assert point["execution_id"] is not None
    assert point["execution_id"] == entries[1]["checks"][0]["series"][0]["execution_id"]


def test_an_unknown_batch_is_a_404(db):
    response = db.client.get("/statistics/batches/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


# --- listing ---


def test_the_list_filters_by_scope_and_status_and_refreshes_first(db):
    first, second = db.test(name="first"), db.test(name="second")
    done = _create(db, test_id=first["id"]).json()
    waiting = _create(db, test_id=second["id"]).json()
    _run_all(db, done["id"])

    everything = db.client.get("/statistics/batches").json()
    passed = db.client.get("/statistics/batches", params={"status": "Passed"}).json()
    by_test = db.client.get("/statistics/batches", params={"test_id": second["id"]}).json()

    assert [b["id"] for b in everything["items"]] == [waiting["id"], done["id"]]
    assert "result" not in everything["items"][0]
    assert [b["id"] for b in passed["items"]] == [done["id"]]
    assert passed["items"][0]["summary"] == "Passed: the check met the goal."
    assert passed["items"][0]["verdicts"] == {"pass": 1, "fail": 0, "inconclusive": 0,
                                              "none": 0}
    assert everything["items"][0]["verdicts"] is None
    assert [b["id"] for b in by_test["items"]] == [waiting["id"]]
    assert by_test["total"] == 1


# --- stopping ---


def test_stopping_cancels_pending_runs_and_lets_a_running_one_finish(db):
    test = db.test()
    batch = _create(db, test_id=test["id"]).json()
    runs = db.runs_of(batch["id"])
    db.execute([runs[0].id])
    db.set_status(runs[1].id, TestStatus.running)

    stopped = db.client.post(f"/statistics/batches/{batch['id']}/stop").json()

    assert stopped["status"] == "Running"
    assert stopped["stopped_at"] is not None
    assert stopped["progress"]["runs"]["NotRan"] == 27
    cancelled = [r for r in db.runs_of(batch["id"]) if r.status == TestStatus.not_ran]
    assert {r.error for r in cancelled} == {"Stopped before it ran: the batch was stopped"}

    # the running one finishes (claimed before the stop); then the batch is Incomplete
    db.set_status(runs[1].id, TestStatus.green)
    read = _get(db, batch["id"])
    assert read["status"] == "Incomplete"
    check = read["result"]["entries"][0]["checks"][0]
    assert check["statistic"]["verdict"] is None
    assert check["counts"]["not_ran"] == 27
    assert read["progress"]["calls"]["application"]["finished"] == 0


def test_a_cancelled_run_is_never_claimed_by_a_worker(db):
    test = db.test()
    batch = _create(db, test_id=test["id"]).json()
    db.client.post(f"/statistics/batches/{batch['id']}/stop")

    _run_all(db, batch["id"])  # the task messages arrive after the stop

    assert {r.status for r in db.runs_of(batch["id"])} == {TestStatus.not_ran}
    assert _get(db, batch["id"])["status"] == "Incomplete"


def test_stopping_a_finished_batch_changes_nothing(db):
    test = db.test()
    batch = _create(db, test_id=test["id"]).json()
    _run_all(db, batch["id"])
    before = _get(db, batch["id"])

    after = db.client.post(f"/statistics/batches/{batch['id']}/stop").json()

    assert after == before
    assert after["stopped_at"] is None


# --- the runs keep their batch ---


def test_a_replay_of_a_batch_execution_is_outside_the_batch(db):
    test_set = db.test_set("support", [db.test()])
    batch = _create(db, test_set_id=test_set["id"]).json()
    execution_id = db.runs_of(batch["id"])[0].test_set_execution_id

    replay = db.client.post(f"/runs/test-sets/{test_set['id']}/executions/{execution_id}")

    assert replay.status_code in (200, 201, 202), replay.text
    assert len(db.runs_of(batch["id"])) == 29


def test_listings_carry_the_batch_and_filter_by_it(db):
    test_set = db.test_set("support", [db.test()])
    ordinary = db.client.post(f"/runs/test-sets/{test_set['id']}").json()
    batch = _create(db, test_set_id=test_set["id"]).json()
    executions = f"/runs/test-sets/{test_set['id']}/executions"

    everything = db.client.get(executions, params={"limit": 500}).json()
    without = db.client.get(executions, params={"batch": "none"}).json()
    only = db.client.get(executions, params={"batch": batch["id"]}).json()

    assert everything["total"] == 30
    assert [(e["id"], e["batch_id"]) for e in without["items"]] == [(ordinary["id"], None)]
    assert only["total"] == 29
    assert sorted(e["batch_index"] for e in only["items"]) == list(range(1, 30))
    assert {e["batch_id"] for e in only["items"]} == {batch["id"]}
    assert db.client.get("/runs/executions", params={"batch": "none"}).json()["total"] == 1
    assert db.client.get(executions, params={"batch": "nope"}).status_code == 422


def test_an_executions_runs_and_their_details_carry_the_batch(db):
    test_set = db.test_set("support", [db.test()])
    plan = db.test_plan("campaign", [test_set])
    set_batch = _create(db, test_set_id=test_set["id"]).json()
    plan_batch = _create(db, test_plan_id=plan["id"]).json()
    set_run = db.runs_of(set_batch["id"])[0]
    plan_run = db.runs_of(plan_batch["id"])[0]
    set_base = f"/runs/test-sets/{test_set['id']}/executions/{set_run.test_set_execution_id}"
    plan_base = f"/runs/test-plans/{plan['id']}/executions/{plan_run.test_plan_execution_id}"

    set_runs = db.client.get(f"{set_base}/test-runs").json()
    set_detail = db.client.get(f"{set_base}/test-runs/{set_run.id}").json()
    plan_runs = db.client.get(f"{plan_base}/test-runs").json()
    plan_detail = db.client.get(f"{plan_base}/test-runs/{plan_run.id}").json()

    assert (set_runs["items"][0]["batch_id"], set_runs["items"][0]["batch_index"]) == (
        set_batch["id"], 1)
    assert (set_detail["batch_id"], set_detail["batch_index"]) == (set_batch["id"], 1)
    assert (plan_runs["items"][0]["batch_id"], plan_runs["items"][0]["batch_index"]) == (
        plan_batch["id"], 1)
    assert (plan_detail["batch_id"], plan_detail["batch_index"]) == (plan_batch["id"], 1)


def test_a_tests_run_listing_filters_by_batch_too(db):
    test = db.test()
    db.client.post(f"/runs/standalone/{test['id']}")
    batch = _create(db, test_id=test["id"]).json()
    runs = f"/runs/standalone/{test['id']}/test-runs"

    assert db.client.get(runs, params={"batch": "none"}).json()["total"] == 1
    listed = db.client.get(runs, params={"batch": batch["id"], "limit": 1}).json()
    assert (listed["total"], listed["items"][0]["batch_id"]) == (29, batch["id"])
    assert db.client.get("/runs", params={"batch": batch["id"]}).json()["total"] == 29
    detail = db.client.get(f"{runs}/{listed['items'][0]['id']}").json()
    assert (detail["batch_id"], detail["batch_index"]) == (batch["id"],
                                                           listed["items"][0]["batch_index"])


# --- review fixes: reads stay cheap, status changes are logged ---


def _stored_status(db, batch_id):
    from sqlalchemy import select

    from assay.models import StatisticalBatchModel
    with db.worker_session() as session:
        return session.scalar(select(StatisticalBatchModel.status).where(
            StatisticalBatchModel.id == uuid.UUID(batch_id)))


def test_the_list_refreshes_only_the_scope_it_was_asked_for(db):
    mine, other = db.test(name="mine"), db.test(name="other")
    _create(db, test_id=mine["id"])
    elsewhere = _create(db, test_id=other["id"]).json()
    _run_all(db, elsewhere["id"])

    db.client.get("/statistics/batches", params={"test_id": mine["id"]})

    assert _stored_status(db, elsewhere["id"]).value == "Pending"  # not touched
    db.client.get("/statistics/batches")
    assert _stored_status(db, elsewhere["id"]).value == "Passed"


def test_a_batch_starting_to_run_is_logged_once(db, caplog):
    test = db.test()
    batch = _create(db, test_id=test["id"]).json()
    db.execute([db.runs_of(batch["id"])[0].id])

    with caplog.at_level("INFO", logger="assay.services.statistics._batches"):
        _get(db, batch["id"])
        _get(db, batch["id"])

    lines = [r.getMessage() for r in caplog.records if "is now" in r.getMessage()]
    assert lines == [f"Batch {batch['id']} is now Running (was Pending)"]


def test_progress_counts_calls_without_reading_results(db):
    db.client.patch("/settings/target", json={"url": "http://127.0.0.1:9/never"})
    asks = db.test(name="asks", model_output=None)
    batch = _create(db, test_id=asks["id"]).json()
    runs = db.runs_of(batch["id"])
    db.execute([runs[0].id])  # the call fails: Not Ran, but it was attempted
    db.set_status(runs[1].id, TestStatus.running)

    progress = _get(db, batch["id"])["progress"]

    assert progress["calls"]["application"] == {"planned": 29, "finished": 1, "in_flight": 1}
    assert progress["runs"]["NotRan"] == 1 and progress["runs_cancelled"] == 0
