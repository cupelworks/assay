"""The estimate planned from the checks' history (notes 30-32): the canvas's
states, through the real endpoint, against the numbers checked by hand."""
import uuid

import pytest

CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}
MENTIONS = {"name": "Contains", "label": "Mentions", "config": {"substring": "order"}}
RELEVANCE = {"name": "Relevance"}
CORRECTNESS = {"name": "Correctness"}


def _history(db, test_set, runs, failing):
    """`runs` executions of the set, every check passing except
    failing[label] times."""
    misses = dict(failing)
    for index in range(runs):
        execution = db.client.post(f"/runs/test-sets/{test_set['id']}").json()
        run = db.client.get(f"/runs/test-sets/{test_set['id']}/executions/{execution['id']}"
                            "/test-runs").json()["items"][0]
        results = {}
        for label in ("Contains", "Correctness", "Mentions", "Relevance"):
            results[label] = {"passed": index >= misses.get(label, 0)}
        db.finish(uuid.UUID(run["id"]), results)


@pytest.fixture
def state_a(db):
    test = db.test(name="Reset a password", model_output=None,
                   checks=[CONTAINS, RELEVANCE, CORRECTNESS, MENTIONS])
    test_set = db.test_set("Support answers", [test])
    _history(db, test_set, 40, {"Relevance": 2, "Correctness": 1})
    return test_set


def _estimate(db, test_set, **body):
    response = db.client.post("/statistics/estimate", json={
        "test_set_id": test_set["id"], "statistical_test": "binomial_gate", **body})
    assert response.status_code == 200, response.text
    return response.json()


def test_out_of_reach_offers_the_size_worth_its_cost_and_says_so(db, state_a):
    estimate = _estimate(db, state_a)

    assert estimate["goal_reachable"] is False
    assert (estimate["best_times"], estimate["best_chance"]) == (977, 0.7114)
    assert [(s["times"], s["kind"], s["default"]) for s in estimate["suggestions"]] == [
        (29, "floor", False), (715, "worth_its_cost", True), (977, "best_chance", False)]
    assert estimate["times"] == 715
    worth = estimate["suggestions"][1]
    assert worth["chance"] == 0.6624
    assert worth["outcome"] == {"passed": 0.5284, "inconclusive": 0.3056, "failed": 0.1661}
    assert worth["likely_undecided"]["label"] == "Relevance"
    assert estimate["odds_summary"] == (
        "Even 977 times give every check an answer only 71% of the time; 715 times give 66% "
        "for 27% fewer runs.")
    assert estimate["driving_check"]["label"] == "Relevance"


def test_each_check_says_what_it_showed_and_how_it_looks(db, state_a):
    checks = {c["label"]: c for c in _estimate(db, state_a)["entries"][0]["checks"]}

    relevance = checks["Relevance"]
    assert relevance["history"] == {"runs": 40, "passed": 38, "rate": 0.95, "mean": None,
                                    "sd": None}
    assert relevance["outlook"] == "too_close"
    assert relevance["outlook_reason"] == (
        "Passed 38 of its last 40 runs: too close to 9 times in 10 to tell cheaply.")
    assert relevance["size_needed"] is None
    assert checks["Correctness"]["size_needed"] == 814
    assert checks["Contains"]["size_needed"] == 142
    assert checks["Contains"]["outlook"] == "likely_pass"


def test_the_cheaper_options_for_the_check_that_drives_the_size(db, state_a):
    estimate = _estimate(db, state_a)
    relevance = next(c for c in estimate["entries"][0]["checks"] if c["label"] == "Relevance")

    by_kind = {(o["kind"], o["target"]): o for o in relevance["cheaper"]}
    eight = by_kind[("lower_target", 0.8)]
    assert (eight["reaches_goal"], round(eight["chance"], 2)) == (False, 0.81)
    left_out = by_kind[("leave_out", None)]
    assert left_out["judge_calls_saved_per_time"] == 1
    assert left_out["reaches_goal"] is False
    less_sure = next(o for o in estimate["cheaper"] if o["kind"] == "less_sure")
    assert less_sure["confidence"] == 0.9


def test_the_curve_peaks_where_one_more_failure_is_allowed(db, state_a):
    odds = _estimate(db, state_a)["odds"]

    assert [p["times"] for p in odds[:4]] == [29, 46, 61, 76]
    assert odds[0]["chance"] == 0.026
    assert max(p["chance"] for p in odds) == 0.7114


def test_within_reach_the_goal_size_is_the_default(db):
    test = db.test(name="steady", model_output=None, checks=[CONTAINS])
    test_set = db.test_set("s", [test])
    _history(db, test_set, 100, {})

    estimate = _estimate(db, test_set)

    assert estimate["goal_reachable"] is True
    assert estimate["times"] == 46
    assert [(s["times"], s["kind"]) for s in estimate["suggestions"]] == [
        (29, "floor"), (46, "reaches_goal")]
    assert estimate["odds_summary"] == "46 times give every check an answer 90% of the time."


def test_one_check_without_history_keeps_the_plain_sizes(db):
    known = db.test(name="known", model_output=None, checks=[CONTAINS])
    new = db.test(name="new", model_output=None, checks=[CONTAINS])
    test_set = db.test_set("s", [known])
    _history(db, test_set, 40, {})
    db.client.post(f"/test-sets/{test_set['id']}/entries", json=[{"id": new["id"]}])

    estimate = _estimate(db, test_set)

    assert estimate["goal_reachable"] is None and estimate["odds"] == []
    outlooks = {e["name"]: e["checks"][0]["outlook"] for e in estimate["entries"]}
    assert outlooks == {"known": "likely_pass", "new": "unknown"}
    assert estimate["suggestions"][0]["kind"] == "floor"


def test_when_no_single_change_reaches_the_goal_the_fewest_left_out_together_do(db, state_a):
    estimate = _estimate(db, state_a)

    together = next(o for o in estimate["cheaper"] if o["kind"] == "leave_out")
    assert sorted(c["label"] for c in together["checks"]) == ["Correctness", "Relevance"]
    assert (together["times"], together["chance"], together["reaches_goal"]) == (
        286, 0.9023, True)
    assert together["judge_calls_saved_per_time"] == 2
    eight = next(o for c in estimate["entries"][0]["checks"] for o in c["cheaper"]
                 if o["kind"] == "lower_target" and o["target"] == 0.8)
    # the ceiling, beside the size it would default to
    assert (eight["best_chance"], eight["best_times"]) == (0.8604, 999)


def test_nothing_is_cheaper_when_nothing_can_vary(db):
    test = db.test(checks=[CONTAINS])
    for _ in range(3):
        run = db.client.post(f"/runs/standalone/{test['id']}").json()
        db.execute([uuid.UUID(run["id"])])

    estimate = db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": "binomial_gate"}).json()

    assert estimate["cheaper"] == []
    assert estimate["entries"][0]["checks"][0]["cheaper"] == []
    assert estimate["entries"][0]["checks"][0]["certain_result"] == "pass"


def test_the_swagger_example_with_history_is_a_real_response(db, state_a):
    from assay.api._estimate_example import ODDS_EVERY, WITH_HISTORY

    db.client.patch("/settings/target", json={"url": "http://app.test/chat"})
    db.client.patch("/settings/judge", json={"provider": "anthropic", "model": "m"})
    real = _estimate(db, state_a)
    real["odds"] = [p for i, p in enumerate(real["odds"])
                    if i % ODDS_EVERY == 0 or p["times"] == real["best_times"]]

    def blank(estimate):
        text = str(estimate)
        entry = estimate["entries"][0]
        for value in (entry["entry_id"], entry["test_id"], entry["test_set_id"],
                      estimate["scope"]["id"]):
            text = text.replace(str(value), "<id>")
        return text

    assert blank(real) == blank(WITH_HISTORY)
