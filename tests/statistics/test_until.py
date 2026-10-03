"""Run until there's an answer (notes 30-32): the waves, their exact
boundaries, the worker advancing a batch after each wave, and the estimate."""
import uuid

import pytest

from assay import sequential
from assay.worker.services.advance_batch import advance_batch, batch_to_advance, stalled_batches

CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}
UNTIL = {"statistical_test": "sequential_gate"}


# --- the arithmetic ---


@pytest.mark.parametrize("maximum", [100, 300, 1000])
def test_the_calibrated_boundaries_keep_each_error_within_the_confidence(maximum):
    plan = sequential.wave_plan(maximum, 0.9, 0.95)

    passes, fails = sequential._errors(plan.looks, 0.9, plan.level)

    assert passes <= 0.05 and fails <= 0.05
    assert plan.maximum == maximum and len(plan.looks) <= sequential.LOOKS
    # stricter than one test of the same size, looser than splitting the error evenly
    assert 0.95 < plan.level < 1 - 0.05 / len(plan.looks) + 1e-9 or len(plan.looks) == 1


def test_a_perfect_record_answers_at_the_first_look_and_a_bad_one_too():
    plan = sequential.wave_plan(300, 0.9, 0.95)
    first = plan.first

    good = sequential.decide([(i, True) for i in range(1, first + 1)], plan, 0.9)
    bad = sequential.decide([(i, i % 2 == 0) for i in range(1, first + 1)], plan, 0.9)

    assert (good.verdict, good.at_time) == ("pass", first)
    assert (bad.verdict, bad.at_time) == ("fail", first)


def test_an_answer_never_changes_after_the_look_that_gave_it():
    plan = sequential.wave_plan(300, 0.9, 0.95)
    outcomes = [(i, True) for i in range(1, plan.first + 1)]
    outcomes += [(i, False) for i in range(plan.first + 1, 301)]

    assert sequential.decide(outcomes, plan, 0.9).verdict == "pass"


def test_the_chances_by_look_grow_and_a_long_history_is_cheap():
    plan = sequential.wave_plan(1000, 0.9, 0.95)

    short = sequential.look_chances(plan, 0.9, 40, 0)
    long = sequential.look_chances(plan, 0.9, 400, 0)

    decided = [short.decided_by(k) for k in range(len(plan.looks))]
    assert decided == sorted(decided)
    assert long.decided_by(0) > short.decided_by(0)


# --- a batch, wave by wave ---


def _create(db, test, maximum=100):
    response = db.client.post("/statistics/batches", json={
        **UNTIL, "test_id": test["id"], "times": maximum})
    assert response.status_code == 202, response.text
    return response.json()


def _advance(db, batch_id):
    published = []
    with db.worker_session() as session:
        advance_batch(uuid.UUID(batch_id), session, published.append)
    return published


def test_only_the_first_wave_is_created(db):
    batch = _create(db, db.test(checks=[CONTAINS]))

    runs = db.runs_of(batch["id"])
    plan = sequential.wave_plan(100, 0.9, 0.95)
    assert len(runs) == plan.first
    assert batch["progress"]["waves"] == {"released": 1, "planned": len(plan.looks),
                                          "looks": list(plan.looks), "closed": False,
                                          "stopped_early": False}
    assert batch["progress"]["times_requested"] == 100


def test_every_check_answered_at_the_first_wave_stops_the_batch(db):
    test = db.test(checks=[CONTAINS])
    batch = _create(db, test)
    runs = db.runs_of(batch["id"])
    db.execute([run.id for run in runs])

    with db.worker_session() as session:
        assert batch_to_advance(runs[-1].id, session) == uuid.UUID(batch["id"])
    assert _advance(db, batch["id"]) == []
    read = db.client.get(f"/statistics/batches/{batch['id']}").json()

    assert read["status"] == "Passed"
    assert read["progress"]["waves"]["closed"] is True
    assert read["progress"]["waves"]["stopped_early"] is True
    check = read["result"]["entries"][0]["checks"][0]
    first = len(runs)
    assert check["statistic"]["reason"] == (
        f"Passed {first} of its first {first} runs: it passes at least 9 times in 10 "
        "(95% sure).")
    assert check["statistic"]["interval"]["level"] < 1


def test_an_undecided_check_gets_the_next_wave_and_the_batch_waits_for_it(db):
    test = db.test(model_output=None, checks=[CONTAINS])
    batch = _create(db, test)
    first = db.runs_of(batch["id"])
    for run in first:  # 9 in 10 at the first look: too close to call
        db.finish(run.id, {"Contains": {"passed": run.batch_index % 10 != 0}})

    published = _advance(db, batch["id"])
    between = db.client.get(f"/statistics/batches/{batch['id']}").json()

    plan = sequential.wave_plan(100, 0.9, 0.95)
    assert len(published) == plan.looks[1] - plan.looks[0]
    assert len(db.runs_of(batch["id"])) == plan.looks[1]
    assert (between["status"], between["result"]) in (("Pending", None), ("Running", None))
    assert between["progress"]["waves"]["released"] == 2
    # the same wave advanced twice: nothing more happens
    assert _advance(db, batch["id"]) == []


def test_between_waves_the_batch_runs_on_without_a_result(db):
    test = db.test(model_output=None, checks=[CONTAINS])
    batch = _create(db, test)
    for run in db.runs_of(batch["id"]):
        db.finish(run.id, {"Contains": {"passed": run.batch_index % 10 != 0}})

    read = db.client.get(f"/statistics/batches/{batch['id']}").json()

    assert (read["status"], read["result"]) == ("Running", None)
    with db.worker_session() as session:
        assert stalled_batches(session) == [uuid.UUID(batch["id"])]


def test_the_last_wave_without_an_answer_is_inconclusive(db):
    test = db.test(model_output=None, checks=[CONTAINS])
    batch = _create(db, test)
    plan = sequential.wave_plan(100, 0.9, 0.95)
    for _ in plan.looks:
        for run in db.runs_of(batch["id"]):
            db.finish(run.id, {"Contains": {"passed": run.batch_index % 10 != 0}})
        _advance(db, batch["id"])

    read = db.client.get(f"/statistics/batches/{batch['id']}").json()

    assert read["status"] == "Inconclusive"
    assert len(db.runs_of(batch["id"])) == 100
    assert read["progress"]["waves"]["stopped_early"] is False
    assert read["result"]["entries"][0]["checks"][0]["statistic"]["reason"].startswith(
        "Can't tell yet: 90 of 100 runs, the most this batch could run")


def test_stop_between_waves_ends_it(db):
    test = db.test(model_output=None, checks=[CONTAINS])
    batch = _create(db, test)
    for run in db.runs_of(batch["id"]):
        db.finish(run.id, {"Contains": {"passed": run.batch_index % 10 != 0}})

    stopped = db.client.post(f"/statistics/batches/{batch['id']}/stop").json()

    assert stopped["status"] == "Incomplete"
    assert stopped["progress"]["waves"]["closed"] is True
    assert _advance(db, batch["id"]) == []


# --- the estimate ---


def test_the_until_estimate_offers_maxima_with_their_chance(db):
    test = db.test(model_output=None, checks=[CONTAINS])
    for _ in range(40):
        run = db.client.post(f"/runs/standalone/{test['id']}").json()
        db.finish(uuid.UUID(run["id"]), {"Contains": {"passed": True}})

    estimate = db.client.post("/statistics/estimate", json={
        **UNTIL, "test_id": test["id"]}).json()
    gate = db.client.post("/statistics/estimate", json={
        "statistical_test": "binomial_gate", "test_id": test["id"]}).json()

    until = estimate["until_answer"]
    assert until["statistical_test"] == "sequential_gate"
    assert estimate["times"] == until["max_times"]
    assert until["chance_by_max"] >= 0.9
    assert until["expected_times"] < until["max_times"]
    assert estimate["floor"] == until["first_wave"]
    assert any(s["default"] and s["kind"] == "reaches_goal" for s in estimate["suggestions"])
    # the fixed test points to its until-there's-an-answer counterpart
    assert gate["until_answer"]["statistical_test"] == "sequential_gate"


def test_judging_the_judge_without_a_judge_says_so(db):
    test = db.test(checks=[CONTAINS])

    responses = [db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": name})
        for name in ("judge_stability", "sequential_judge_stability")]

    messages = [r.json()["detail"][0]["msg"] for r in responses]
    assert messages[0] == messages[1]
    assert messages[0].startswith("None of these checks is an LLM judge on a recorded answer")


def test_leaving_out_the_only_judged_check_says_that(db):
    test = db.test(checks=[{"name": "Toxicity"}, CONTAINS])

    response = db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": "judge_stability",
        "leave_out": [{"label": "Toxicity"}]})

    assert response.json()["detail"][0]["msg"] == (
        "Every check this test can judge is left out: keep at least one")
