import pytest

from assay.services.statistics.catalogue import MAX_RUNS, MAX_TIMES

CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}
ROUGE = {"name": "ROUGE", "config": {"threshold": "0.5"}}
TOXICITY = {"name": "Toxicity"}


def _estimate(db, **body):
    return db.client.post("/statistics/estimate", json=body)


def _messages(response):
    return [(tuple(item["loc"]), item["msg"]) for item in response.json()["detail"]]


# --- the catalogue ---


def test_the_catalogue_lists_each_test_with_its_parameters_and_floor(db):
    catalogue = db.client.get("/statistics/tests").json()

    by_id = {item["id"]: item for item in catalogue["items"]}
    assert set(by_id) == {"binomial_gate", "one_sample_t", "judge_stability", "trial",
                          "sequential_gate", "sequential_judge_stability", "pass_rates",
                          "no_worse", "mean_scores", "score_ranks", "paired_entries"}
    gate = by_id["binomial_gate"]
    assert (gate["kind"], gate["reads"], gate["applies_to"]) == (
        "batch", "pass_fail", "every_check")
    assert [p["key"] for p in gate["parameters"]] == ["target", "confidence"]
    assert gate["floor"]["kind"] == "exact"
    assert {"target": 0.9, "confidence": 0.95, "times": 29} in gate["floor"]["examples"]
    assert by_id["one_sample_t"]["recommended_times"] == 30
    assert by_id["pass_rates"]["kind"] == "comparison"
    assert (catalogue["max_times"], catalogue["max_runs"]) == (MAX_TIMES, MAX_RUNS)


def test_the_catalogues_worked_examples_are_what_the_estimate_computes(db):
    # no history, so the sizes come from the floor alone: an answer the
    # application gives (it can vary) for the gate and the t-test, a recorded
    # one with a judge for judge stability
    varies = db.test(name="varies", model_output=None, checks=[ROUGE, TOXICITY])
    recorded = db.test(name="recorded", checks=[ROUGE, TOXICITY])
    for item in db.client.get("/statistics/tests").json()["items"]:
        if item["kind"] != "batch":
            continue
        test = recorded if item["engine"] == "judge_stability" else varies
        for example in item["floor"]["examples"]:
            parameters = {k: v for k, v in example.items() if k != "times"}
            estimate = _estimate(db, test_id=test["id"], statistical_test=item["id"],
                                 parameters=parameters).json()
            default = next(s["times"] for s in estimate["suggestions"] if s["default"])
            assert default == example["times"], (item["id"], example)


# --- sizing ---


def test_the_gate_suggests_the_floor_one_spare_and_one_miss(db):
    test = db.test(model_output=None)

    estimate = _estimate(db, test_id=test["id"], statistical_test="binomial_gate").json()

    assert estimate["parameters"] == {"target": 0.9, "confidence": 0.95}
    assert estimate["floor"] == 29
    assert [(s["times"], s["kind"], s["default"]) for s in estimate["suggestions"]] == [
        (29, "floor", True), (30, "absorbs_not_ran", False), (46, "allows_one_miss", False)]
    assert estimate["times"] == 29
    assert estimate["rule"] == {"times": 29, "pass_at_least": 29, "fail_at_most": 22}
    assert estimate["floor_explanation"] == (
        "A few in a row can be luck. A check that really passed only 9 times in 10 would pass "
        "29 times in a row less than 1 time in 20, so 29 out of 29 is enough to be 95% sure. "
        "With fewer, even a perfect record could be luck.")
    assert [s["label"] for s in estimate["suggestions"]] == [
        "29 times · no failure allowed", "30 times · one spare, in case a run can't run",
        "46 times · one failure allowed"]


def test_the_gates_rule_follows_the_times_asked(db):
    test = db.test()

    estimate = _estimate(db, test_id=test["id"], statistical_test="binomial_gate",
                         times=46).json()

    assert estimate["rule"] == {"times": 46, "pass_at_least": 45, "fail_at_most": 37}


@pytest.mark.parametrize("target,floor", [(0.8, 14), (0.95, 59), (0.99, 299)])
def test_the_gates_floor_follows_the_target(db, target, floor):
    test = db.test(model_output=None)

    estimate = _estimate(db, test_id=test["id"], statistical_test="binomial_gate",
                         parameters={"target": target}).json()

    assert estimate["floor"] == floor


def test_the_t_test_suggests_its_floor_the_size_that_sees_the_gap_and_30(db):
    test = db.test(model_output=None, checks=[ROUGE])

    estimate = _estimate(db, test_id=test["id"], statistical_test="one_sample_t").json()

    assert estimate["parameters"] == {"confidence": 0.95, "difference": 0.05, "spread": 0.1}
    assert [(s["times"], s["kind"], s["default"]) for s in estimate["suggestions"]] == [
        (10, "floor", False), (27, "detects_difference", True), (30, "recommended", False)]
    assert (estimate["floor"], estimate["times"], estimate["rule"]) == (10, 27, None)


def test_a_t_test_suggestion_never_goes_below_its_floor(db):
    test = db.test(model_output=None, checks=[ROUGE])

    estimate = _estimate(db, test_id=test["id"], statistical_test="one_sample_t",
                         parameters={"difference": 0.2}).json()

    assert [(s["times"], s["default"]) for s in estimate["suggestions"]] == [
        (10, True), (30, False)]


# --- the scope and the cost ---


def test_a_test_set_costs_one_run_per_entry_and_one_judge_call_per_judge_check(db):
    recorded = db.test(name="recorded", checks=[CONTAINS, TOXICITY])
    asks = db.test(name="asks", model_output=None, checks=[CONTAINS])
    test_set = db.test_set("support", [recorded, asks])

    estimate = _estimate(db, test_set_id=test_set["id"], statistical_test="binomial_gate",
                         times=30).json()

    assert estimate["scope"] == {"kind": "test_set", "id": test_set["id"], "name": "support"}
    assert (estimate["runs_per_time"], estimate["runs_total"]) == (2, 60)
    assert estimate["calls"] == {"application": {"per_time": 1, "total": 30},
                                 "judge": {"per_time": 1, "total": 30}}
    assert (estimate["checks_total"], estimate["checks_applicable"]) == (3, 3)
    assert [(e["name"], e["recorded_answer"]) for e in estimate["entries"]] == [
        ("asks", False), ("recorded", True)]


def test_a_test_plan_counts_every_entry_of_every_linked_set(db):
    first = db.test_set("a", [db.test(name="one"), db.test(name="two")])
    second = db.test_set("b", [db.test(name="three")])
    plan = db.test_plan("campaign", [first, second])

    estimate = _estimate(db, test_plan_id=plan["id"], statistical_test="binomial_gate").json()

    assert estimate["scope"]["kind"] == "test_plan"
    assert estimate["runs_per_time"] == 3
    assert [(e["test_set_name"], e["name"]) for e in estimate["entries"]] == [
        ("a", "one"), ("a", "two"), ("b", "three")]


def test_a_t_test_applies_only_to_scored_checks(db):
    test = db.test(checks=[ROUGE, CONTAINS])

    estimate = _estimate(db, test_id=test["id"], statistical_test="one_sample_t").json()

    checks = {c["label"]: c for c in estimate["entries"][0]["checks"]}
    assert checks["ROUGE"]["applies"] is True
    assert {k: checks["Contains"][k] for k in ("label", "test_type", "applies", "reason")} == {
        "label": "Contains", "test_type": "Contains", "applies": False,
        "reason": "It only passes or fails: an average needs a check that gives a score"}
    assert checks["Contains"]["outlook"] is None
    assert estimate["checks_applicable"] == 1
    assert "checks_not_applicable" in [w["code"] for w in estimate["warnings"]]


# --- warnings ---


def _codes(estimate):
    return [w["code"] for w in estimate["warnings"]]


def test_a_recorded_answer_without_a_judge_can_never_vary(db):
    test = db.test()

    estimate = _estimate(db, test_id=test["id"], statistical_test="binomial_gate").json()

    assert _codes(estimate) == ["recorded_answers", "nothing_can_vary"]
    assert estimate["warnings"][0]["message"] == (
        "The test has a recorded answer: only its judge checks can vary between runs.")


def test_judge_checks_without_a_judge_are_flagged(db):
    test = db.test(checks=[TOXICITY])

    codes = _codes(_estimate(db, test_id=test["id"], statistical_test="binomial_gate").json())

    assert codes == ["recorded_answers", "no_judge_configured"]


def test_answers_from_an_application_that_isnt_configured_are_flagged(db):
    test = db.test(model_output=None)

    codes = _codes(_estimate(db, test_id=test["id"], statistical_test="binomial_gate").json())

    assert codes == ["no_application_configured"]


def test_nothing_to_flag_when_the_application_and_the_judge_are_set(db):
    db.client.patch("/settings/target", json={"url": "http://app.test/chat"})
    db.client.patch("/settings/judge", json={"provider": "anthropic", "model": "m"})
    test = db.test(model_output=None, checks=[TOXICITY])

    codes = _codes(_estimate(db, test_id=test["id"], statistical_test="binomial_gate").json())

    assert codes == []


# --- guards ---


def test_an_unknown_scope_is_a_404(db):
    response = _estimate(db, test_set_id="00000000-0000-0000-0000-000000000000",
                         statistical_test="binomial_gate")

    assert response.status_code == 404


def test_an_empty_test_set_is_a_409(db):
    test_set = db.test_set("empty", [])

    response = _estimate(db, test_set_id=test_set["id"], statistical_test="binomial_gate")

    assert response.status_code == 409


def test_exactly_one_scope(db):
    test = db.test()
    assert _estimate(db, statistical_test="binomial_gate").status_code == 422
    assert _estimate(db, test_id=test["id"], test_set_id=test["id"],
                     statistical_test="binomial_gate").status_code == 422


def test_times_below_the_floor_is_a_422_naming_the_floor(db):
    test = db.test()

    response = _estimate(db, test_id=test["id"], statistical_test="binomial_gate", times=28)

    assert response.status_code == 422
    assert _messages(response) == [(("body", "times"),
                                    "Passes reliably needs at least 29 times with these "
                                    "settings: with fewer, no result could give an answer")]


def test_parameters_out_of_range_and_unknown_are_listed_together(db):
    test = db.test()

    response = _estimate(db, test_id=test["id"], statistical_test="binomial_gate",
                         parameters={"target": 1.0, "confidence": 0.5, "extra": 1})

    assert response.status_code == 422
    assert [loc for loc, _ in _messages(response)] == [
        ("body", "parameters", "extra"), ("body", "parameters", "target"),
        ("body", "parameters", "confidence")]


def test_a_floor_above_the_batch_limit_is_a_422(db):
    test = db.test()

    response = _estimate(db, test_id=test["id"], statistical_test="binomial_gate",
                         parameters={"target": 0.999})

    assert response.status_code == 422
    assert "at least 2995 times" in _messages(response)[0][1]


def test_more_runs_than_a_batch_may_create_is_a_422(db):
    test_set = db.test_set("many", [db.test(name=f"t{i}") for i in range(11)])

    response = _estimate(db, test_set_id=test_set["id"], statistical_test="binomial_gate",
                         times=1000)

    assert response.status_code == 422
    assert _messages(response) == [(("body", "times"),
                                    "1000 times × 11 runs each is 11000 runs, more than the "
                                    "10000 a batch can create: at most 909 times for this "
                                    "scope")]


def test_a_comparison_test_isnt_a_batch_test(db):
    test = db.test()

    response = _estimate(db, test_id=test["id"], statistical_test="pass_rates")

    assert response.status_code == 422
    assert _messages(response)[0][0] == ("body", "statistical_test")


def test_a_t_test_needs_a_scored_check(db):
    test = db.test()

    response = _estimate(db, test_id=test["id"], statistical_test="one_sample_t")

    assert response.status_code == 422
    assert "an average needs ROUGE" in _messages(response)[0][1]


def test_the_estimate_creates_nothing(db):
    test = db.test()

    _estimate(db, test_id=test["id"], statistical_test="binomial_gate")

    assert db.client.get(f"/runs/standalone/{test['id']}/test-runs").json()["total"] == 0
    assert db.dispatched == []


def test_judge_stability_applies_to_judge_checks_of_recorded_answers_only(db):
    recorded = db.test(name="recorded", checks=[CONTAINS, TOXICITY])
    asks = db.test(name="asks", model_output=None, checks=[TOXICITY])
    test_set = db.test_set("judged", [recorded, asks])

    estimate = _estimate(db, test_set_id=test_set["id"],
                         statistical_test="judge_stability").json()

    checks = {(e["name"], c["label"]): (c["applies"], c["reason"])
              for e in estimate["entries"] for c in e["checks"]}
    assert checks == {
        ("asks", "Toxicity"): (False, "The answer changes from run to run, so a changed "
                                      "verdict could be the answer's doing, not the judge's"),
        ("recorded", "Contains"): (False, "Not an LLM judge: this test checks whether the "
                                          "judge is consistent"),
        ("recorded", "Toxicity"): (True, None),
    }
    assert estimate["floor"] == 29


def test_judge_stability_needs_a_judge_check_on_a_recorded_answer(db):
    test = db.test()

    response = _estimate(db, test_id=test["id"], statistical_test="judge_stability")

    assert response.status_code == 422
    assert "this test needs one" in _messages(response)[0][1]


# --- review fixes: a default the API would refuse ---


def test_a_t_test_gap_too_small_for_one_batch_defaults_to_the_most_a_batch_can_run(db):
    test = db.test(model_output=None, checks=[ROUGE])

    response = _estimate(db, test_id=test["id"], statistical_test="one_sample_t",
                         parameters={"difference": 0.005})

    assert response.status_code == 200, response.text
    estimate = response.json()
    default = next(s for s in estimate["suggestions"] if s["default"])
    assert default["times"] == estimate["times"] == MAX_TIMES
    assert default["label"].startswith(f"{MAX_TIMES} times · the most a batch can run")
    assert "1000 times will often miss a gap that small" in estimate["floor_explanation"]


def test_a_default_bigger_than_the_scope_can_run_is_capped_with_a_warning(db):
    scored = db.test_set("scored", [db.test(name=f"s{i}", model_output=None, checks=[ROUGE])
                                    for i in range(11)])

    response = _estimate(db, test_set_id=scored["id"], statistical_test="one_sample_t",
                         parameters={"difference": 0.005})

    assert response.status_code == 200, response.text
    estimate = response.json()
    assert estimate["times"] == MAX_RUNS // 11
    capped = next(w for w in estimate["warnings"] if w["code"] == "times_capped")
    assert capped["message"] == (
        "The suggested 1000 times would create 11000 runs, more than the 10000 a batch can "
        "create: this estimate is for 909 times, the most for this scope.")


def test_judge_stability_shows_its_rule_too(db):
    test = db.test(checks=[TOXICITY])

    estimate = _estimate(db, test_id=test["id"], statistical_test="judge_stability").json()

    assert estimate["rule"] == {"times": 29, "pass_at_least": 29, "fail_at_most": 22}
