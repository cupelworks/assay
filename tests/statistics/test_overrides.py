"""A check's own target and checks left out of a batch (note 32): checked
against the scope, stored on the batch, skipped by the runs, honoured by the
result."""
CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}
MISSES = {"name": "Contains", "label": "Misses", "config": {"substring": "absent"}}
TOXICITY = {"name": "Toxicity"}


def _post(db, path, **body):
    return db.client.post(path, json={"statistical_test": "binomial_gate", **body})


def _messages(response):
    return [(tuple(item["loc"]), item["msg"]) for item in response.json()["detail"]]


def test_the_estimate_shows_a_checks_own_target_and_a_left_out_check(db):
    test = db.test(checks=[CONTAINS, MISSES, TOXICITY])

    estimate = _post(db, "/statistics/estimate", test_id=test["id"],
                     targets=[{"label": "Contains", "target": 0.8}],
                     leave_out=[{"label": "Toxicity"}]).json()

    checks = {c["label"]: c for c in estimate["entries"][0]["checks"]}
    assert (checks["Contains"]["target"], checks["Misses"]["target"]) == (0.8, 0.9)
    assert (checks["Toxicity"]["applies"], checks["Toxicity"]["left_out"],
            checks["Toxicity"]["reason"]) == (False, True, "Left out of this batch")
    assert estimate["calls"]["judge"]["per_time"] == 0
    assert "checks_not_applicable" not in [w["code"] for w in estimate["warnings"]]


def test_overrides_are_checked_against_the_scope_all_at_once(db):
    test = db.test(checks=[CONTAINS])

    response = _post(db, "/statistics/estimate", test_id=test["id"],
                     targets=[{"label": "Nope", "target": 0.8},
                              {"label": "Contains", "target": 0.2}],
                     leave_out=[{"label": "Contains"}])

    assert response.status_code == 422
    assert _messages(response) == [
        (("body", "targets", 0, "label"), "No check 'Nope' in this scope"),
        (("body", "targets", 1, "target"), "Must be between 0.5 and 0.999"),
        (("body", "leave_out"), "Every check of 't' is left out: keep at least one"),
    ]


def test_a_test_without_a_target_takes_none_per_check(db):
    test = db.test(checks=[{"name": "ROUGE", "config": {"threshold": "0.5"}}])

    response = db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": "one_sample_t",
        "targets": [{"label": "ROUGE", "target": 0.8}]})

    assert _messages(response) == [
        (("body", "targets", 0), "Scores high enough on average has no target to set per check")]


def test_a_batch_keeps_its_overrides_and_its_runs_skip_the_left_out_check(db):
    test = db.test(checks=[CONTAINS, MISSES])
    batch = _post(db, "/statistics/batches", test_id=test["id"],
                  targets=[{"label": "Contains", "target": 0.8}],
                  leave_out=[{"label": "Misses"}]).json()

    runs = db.runs_of(batch["id"])
    db.execute([run.id for run in runs])
    read = db.client.get(f"/statistics/batches/{batch['id']}").json()

    # the only check judged is at 8 in 10, and it can't vary: its floor answers it
    assert len(runs) == 14
    assert read["targets"] == [{"entry_id": None, "label": "Contains", "target": 0.8}]
    assert read["leave_out"] == [{"entry_id": None, "label": "Misses"}]
    checks = {c["label"]: c for c in read["result"]["entries"][0]["checks"]}
    assert (checks["Contains"]["target"], checks["Contains"]["statistic"]["verdict"]) == (
        0.8, "pass")
    assert checks["Misses"]["applies"] is False
    assert checks["Misses"]["reason"] == "Left out of this batch"
    assert checks["Misses"]["counts"]["evaluated"] == 0
    assert read["status"] == "Passed"
    run = db.client.get(f"/runs/standalone/{test['id']}/test-runs/{runs[0].id}").json()
    assert set(run["results"]) == {"Contains"}


def test_a_set_batch_skips_a_check_only_for_its_entry(db):
    a = db.test(name="a", checks=[CONTAINS, MISSES])
    b = db.test(name="b", checks=[CONTAINS, MISSES])
    test_set = db.test_set("s", [a, b])
    entries = db.client.post("/statistics/estimate", json={
        "test_set_id": test_set["id"], "statistical_test": "binomial_gate"}).json()["entries"]
    entry_a = next(e["entry_id"] for e in entries if e["name"] == "a")

    batch = _post(db, "/statistics/batches", test_set_id=test_set["id"],
                  leave_out=[{"entry_id": entry_a, "label": "Misses"}]).json()

    skips = {str(run.test_set_entry_id): None for run in db.runs_of(batch["id"])}
    from sqlalchemy import select

    from assay.models import TestRunModel
    with db.worker_session() as session:
        rows = session.execute(select(TestRunModel.test_set_entry_id, TestRunModel.skip_labels)
                               .where(TestRunModel.batch_id == __import__("uuid").UUID(
                                   batch["id"]))).all()
    for entry_id, skip in rows:
        skips[str(entry_id)] = skip
    assert skips[entry_a] == ["Misses"]
    assert [skip for entry, skip in skips.items() if entry != entry_a] == [None]


def test_a_left_out_check_still_shows_what_it_did(db):
    import uuid
    test = db.test(model_output=None, checks=[CONTAINS, MISSES])
    for index in range(5):
        run = db.client.post(f"/runs/standalone/{test['id']}").json()
        db.finish(uuid.UUID(run["id"]), {"Contains": {"passed": True},
                                         "Misses": {"passed": index > 0}})

    estimate = _post(db, "/statistics/estimate", test_id=test["id"],
                     leave_out=[{"label": "Misses"}]).json()

    misses = next(c for c in estimate["entries"][0]["checks"] if c["label"] == "Misses")
    assert (misses["left_out"], misses["applies"]) == (True, False)
    assert misses["history"] == {"runs": 5, "passed": 4, "rate": 0.8, "mean": None, "sd": None}
    assert misses["outlook"] is not None
    assert (misses["size_needed"], misses["cheaper"]) == (None, [])
