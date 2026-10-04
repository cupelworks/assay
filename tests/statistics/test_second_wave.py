"""The second wave end to end on a real database: judge stability, the
failures-by-entry diagnostic, and the comparisons of scores and pairs. Judge
and metric outcomes are written as the worker would write them: this
environment has no judge to call."""
ROUGE = {"name": "ROUGE", "config": {"threshold": "0.5"}}
TOXICITY = {"name": "Toxicity"}
CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}


def _batch(db, statistical_test="binomial_gate", **body) -> dict:
    response = db.client.post("/statistics/batches",
                              json={"statistical_test": statistical_test, **body})
    assert response.status_code == 202, response.text
    return response.json()


def _get(db, batch) -> dict:
    return db.client.get(f"/statistics/batches/{batch['id']}").json()


def test_a_judge_that_changes_its_mind_fails_judge_stability(db):
    test = db.test(checks=[TOXICITY])
    batch = _batch(db, "judge_stability", test_id=test["id"])
    for run in db.runs_of(batch["id"]):
        # passes 20 times of 29, fails 9: 69% agreement against a 90% target
        db.finish(run.id, {"Toxicity": {"passed": run.batch_index <= 20}})

    read = _get(db, batch)

    assert read["status"] == "Failed"
    check = read["result"]["entries"][0]["checks"][0]
    assert check["statistic"]["verdict"] == "fail"
    assert check["statistic"]["reason"].startswith(
        "The judge is inconsistent: 20 of 29 runs said pass. It agrees with itself less than "
        "9 times in 10 (95% sure).")
    assert check["agreement"]["point"] == 0.6897
    assert check["target"] == 0.9


def test_a_set_batch_says_where_failures_concentrate(db):
    test_set = db.test_set("support", [db.test(name="steady"), db.test(name="flaky")])
    estimate = db.client.post("/statistics/estimate", json={
        "test_set_id": test_set["id"], "statistical_test": "binomial_gate"}).json()
    flaky = next(e["entry_id"] for e in estimate["entries"] if e["name"] == "flaky")
    batch = _batch(db, test_set_id=test_set["id"])
    for run in db.runs_of(batch["id"]):
        failing = str(run.test_set_entry_id) == flaky and run.batch_index % 2 == 0
        db.finish(run.id, {"Contains": {"passed": not failing}})

    diagnostic = _get(db, batch)["result"]["failures_by_entry"]

    assert diagnostic["verdict"] == "concentrated"
    assert [(e["name"], e["passed"], e["failed"]) for e in diagnostic["entries"]] == [
        ("flaky", 15, 14), ("steady", 29, 0)]
    assert diagnostic["reason"].startswith("Most failures come from a few entries: flaky")


def _scored_batch(db, test, scores) -> dict:
    batch = _batch(db, "one_sample_t", test_id=test["id"], times=len(scores))
    for run, score in zip(db.runs_of(batch["id"]), scores, strict=True):
        db.finish(run.id, {"ROUGE": {"passed": score >= 0.5, "score": score}})
    return batch


def test_mean_scores_and_score_ranks_compare_two_batches_of_scores(db):
    test = db.test(checks=[ROUGE])
    a = _scored_batch(db, test, [0.61, 0.58, 0.66, 0.55, 0.63, 0.6, 0.57, 0.64, 0.59, 0.62])
    b = _scored_batch(db, test, [0.7, 0.65, 0.72, 0.61, 0.69, 0.74, 0.66, 0.7, 0.68, 0.71])

    for test_name, method in (("mean_scores", "welch"), ("score_ranks", "mann_whitney_normal")):
        response = db.client.post("/statistics/comparisons", json={
            "batch_a": a["id"], "batch_b": b["id"], "statistical_test": test_name})
        assert response.status_code == 201, response.text
        check = response.json()["result"]["entries"][0]["checks"][0]
        assert (check["verdict"], check["p_value_method"]) == ("better", method)
        assert check["a"]["scores"]["mean"] == 0.605


def test_no_worse_takes_its_margin(db):
    test = db.test(checks=[CONTAINS])
    a = _batch(db, test_id=test["id"])
    b = _batch(db, test_id=test["id"])
    for batch in (a, b):
        db.execute([run.id for run in db.runs_of(batch["id"])])

    response = db.client.post("/statistics/comparisons", json={
        "batch_a": a["id"], "batch_b": b["id"], "statistical_test": "no_worse",
        "parameters": {"margin": 0.15}})

    assert response.status_code == 201, response.text
    comparison = response.json()
    assert comparison["parameters"] == {"margin": 0.15, "confidence": 0.95}
    assert comparison["result"]["entries"][0]["checks"][0]["verdict"] == "no_worse"
    assert comparison["summary"] == "B is still as good as A on the check."


def test_paired_entries_on_a_set(db):
    tests = [db.test(name=f"entry {i}") for i in range(6)]
    test_set = db.test_set("support", tests)
    a = _batch(db, test_set_id=test_set["id"])
    b = _batch(db, test_set_id=test_set["id"])
    for batch, passes in ((a, lambda i: i % 3 != 0), (b, lambda i: True)):
        for run in db.runs_of(batch["id"]):
            db.finish(run.id, {"Contains": {"passed": passes(run.batch_index)}})

    response = db.client.post("/statistics/comparisons", json={
        "batch_a": a["id"], "batch_b": b["id"], "statistical_test": "paired_entries"})

    paired = response.json()["result"]["paired"]
    assert paired["n_pairs"] == 6
    assert paired["verdict"] == "better"
    assert response.json()["result"]["entries"][0]["checks"][0]["verdict"] is None
