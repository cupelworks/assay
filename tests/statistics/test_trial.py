"""The trial (note 32): a few runs with no verdict, offered when a check has
never run, ending Done, and its runs the history the next estimate plans
from."""

CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}


def test_the_catalogue_lists_the_trial_after_the_first_batch_tests(db):
    items = db.client.get("/statistics/tests").json()["items"]

    batch_tests = [i["id"] for i in items if i["kind"] == "batch"]
    assert batch_tests == ["binomial_gate", "one_sample_t", "judge_stability", "trial",
                           "sequential_gate", "sequential_judge_stability",
                           "sequential_t"]
    trial = next(i for i in items if i["id"] == "trial")
    assert (trial["name"], trial["engine"], trial["parameters"], trial["verdicts"]) == (
        "Learn how it behaves", "trial", [], [])
    assert trial["engine_settings"] == {"default_times": 10, "max_times": 50}


def test_a_check_with_no_history_is_offered_a_trial(db):
    test = db.test(model_output=None, checks=[CONTAINS])

    estimate = db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": "binomial_gate"}).json()

    assert estimate["trial"] == {
        "statistical_test": "trial", "times": 10, "application_calls": 10, "judge_calls": 0,
        "reason": "1 check has never run: a trial of 10 times shows how they behave, so the "
                  "batch can be planned from it."}


def test_a_trial_runs_gives_no_verdict_and_is_done(db):
    test = db.test(checks=[CONTAINS])

    estimate = db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": "trial"}).json()
    batch = db.client.post("/statistics/batches", json={
        "test_id": test["id"], "statistical_test": "trial"}).json()
    db.execute([run.id for run in db.runs_of(batch["id"])])
    done = db.client.get(f"/statistics/batches/{batch['id']}").json()

    assert (estimate["times"], estimate["suggestions"][0]["kind"]) == (10, "trial")
    assert estimate["warnings"] == [w for w in estimate["warnings"]
                                    if w["code"] != "checks_not_applicable"]
    assert done["status"] == "Done"
    assert done["summary"] == "Done: ran 10 times — passed: Contains 10 of 10."
    check = done["result"]["entries"][0]["checks"][0]
    assert (check["applies"], check["statistic"]) == (False, None)
    assert check["pass_rate"]["point"] == 1.0
    assert done["verdicts"] == {"pass": 0, "fail": 0, "inconclusive": 0, "none": 0}


def test_a_trial_is_at_most_fifty_times(db):
    test = db.test(checks=[CONTAINS])

    response = db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": "trial", "times": 51})

    assert response.json()["detail"][0]["msg"] == "At most 50 times for a trial"


def test_after_a_trial_the_estimate_plans_from_its_runs(db):
    test = db.test(model_output=None, checks=[CONTAINS])
    batch = db.client.post("/statistics/batches", json={
        "test_id": test["id"], "statistical_test": "trial"}).json()
    for run in db.runs_of(batch["id"]):
        db.finish(run.id, {"Contains": {"passed": True}})

    estimate = db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": "binomial_gate"}).json()

    assert estimate["trial"] is None
    assert estimate["entries"][0]["checks"][0]["history"]["runs"] == 10
    assert estimate["goal_reachable"] is not None
