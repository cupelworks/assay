# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The gate's decisions on the API's replies, and the reports written from them:
the cases a real batch is slow or awkward to bring about."""
from collections import Counter
from xml.etree import ElementTree as ET

import pytest

from assay_cli import report
from assay_cli.gate import (
    STRICT,
    Judged,
    Limits,
    Policy,
    PolicyError,
    batch_finished,
    batch_outcome,
    check_blocking_checks,
    check_test_sets,
    execution_outcome,
    judge,
    run_outcome,
)


def _batch(status, verdicts=None, checks=()):
    result = None if status in ("Pending", "Running", "Incomplete") else {
        "verdicts": verdicts or {}, "summary": f"It's {status}.",
        "entries": [{"entry_id": "e", "test_set_name": "Smoke", "name": "a",
                     "checks": list(checks)}]}
    return {"id": "b1", "status": status, "statistical_test": "binomial_gate",
            "scope": {"kind": "test_set", "id": "s1", "name": "Smoke"}, "result": result,
            "summary": None if result is None else result["summary"]}


def _check(label, verdict, applies=True):
    return {"label": label, "applies": applies, "reason": None if applies else "not scored",
            "statistic": {"verdict": verdict, "reason": f"{label} is {verdict}"}}


@pytest.mark.parametrize(("status", "fail_on", "passed"), [
    ("Passed", ("fail",), True),
    ("Done", ("fail",), True),
    ("Failed", ("fail",), False),
    ("Inconclusive", ("fail",), True),
    ("Inconclusive", ("fail", "inconclusive"), False),
    ("Incomplete", ("fail",), False),
    ("NotRan", ("fail",), False),
])


def test_a_batch_status_against_fail_on(status, fail_on, passed):
    assert batch_outcome(_batch(status), fail_on).passed is passed


def test_a_stopped_batch_says_why_it_fails():
    outcome = batch_outcome(_batch("Incomplete"))
    assert "stopped before every run ran" in outcome.summary


def test_a_batchs_checks_become_cases():
    checks = [_check("A", "pass"), _check("B", "fail"), _check("C", "inconclusive"),
              _check("D", None, applies=False)]
    outcome = batch_outcome(_batch("Failed", {"pass": 1, "fail": 1}, checks),
                            ("fail", "inconclusive"))

    assert [(c.name, c.outcome) for c in outcome.cases] == [
        ("A", "passed"), ("B", "failed"), ("C", "failed"), ("D", "skipped")]
    assert "Smoke / a › B: fail: B is fail" in report.text(outcome)
    suite = ET.fromstring(report.junit(outcome)).find("testsuite")
    assert (suite.get("failures"), suite.get("skipped")) == ("2", "1")


def test_a_batch_is_finished_once_it_has_a_result_or_a_final_status():
    assert not batch_finished(_batch("Running"))
    assert batch_finished(_batch("Incomplete"))
    assert batch_finished(_batch("Passed"))


def test_a_run_that_couldnt_run_is_a_junit_error():
    scope = {"kind": "test-set", "id": "s", "name": "Smoke"}
    details = {"id": "x", "run_count": 1,
               "runs": {"Pending": 0, "Running": 0, "Green": 0, "Amber": 0, "Red": 0,
                        "NotRan": 1}}
    runs = [{"id": "r", "test_name": "a", "status": "NotRan", "checks": None,
             "error": "No target URL is set"}]

    outcome = execution_outcome(scope, details, runs)

    assert not outcome.passed and outcome.status == "NotRan"
    assert "! a: Not Ran: No target URL is set" in report.text(outcome)
    error = ET.fromstring(report.junit(outcome)).find(".//testcase/error")
    assert error.get("message") == "Not Ran: No target URL is set"
    assert "| ! | a | Not Ran: No target URL is set |" in report.markdown(outcome)


@pytest.mark.parametrize(("status", "verdicts", "fail_on", "passed"), [
    ("Failed", {"fail": 1, "inconclusive": 3}, ("inconclusive",), False),
    ("Failed", {"fail": 1}, ("inconclusive",), False),  # a proven failure always fails
    ("Inconclusive", {"pass": 1, "inconclusive": 2}, ("fail",), True),
    ("Inconclusive", {"pass": 1, "inconclusive": 2}, ("fail", "inconclusive"), False),
    ("Passed", {"pass": 2}, ("fail", "inconclusive"), True),
    ("Inconclusive", {"pass": 2, "none": 1}, ("fail", "inconclusive"), False),
    ("Inconclusive", {"pass": 2, "none": 1}, ("fail",), True),
])


def test_a_batch_is_gated_on_every_checks_verdict(status, verdicts, fail_on, passed):
    assert batch_outcome(_batch(status, verdicts), fail_on).passed is passed


@pytest.mark.parametrize("status", ["NotRan", "Incomplete"])


def test_a_batch_that_always_fails_reports_why_as_an_error(status):
    outcome = batch_outcome(_batch(status, checks=[_check("A", None)]))

    assert not outcome.passed
    assert [(c.name, c.outcome) for c in outcome.failures] == [("binomial_gate", "error")]
    text = report.text(outcome)
    assert f"! Smoke › binomial_gate: {status}: " in text
    assert "fails on:" not in text
    suite = ET.fromstring(report.junit(outcome)).find("testsuite[@name='Smoke']")
    assert suite.get("errors") == "1"


def test_same_named_tests_in_an_execution_get_distinct_junit_names():
    scope = {"kind": "test-plan", "id": "p", "name": "Release"}
    details = {"id": "x", "run_count": 3,
               "runs": {"Green": 2, "Red": 1, "Amber": 0, "NotRan": 0}}
    runs = [{"id": "r1", "test_set_entry_id": {"id": "e1"}, "test_name": "Reset a password",
             "status": "Red", "checks": {"not_met": ["Contains"]}},
            {"id": "r2", "test_set_entry_id": {"id": "e2"}, "test_name": "Reset a password",
             "status": "Green", "checks": {"not_met": []}},
            {"id": "r3", "test_set_entry_id": {"id": "e3"}, "test_name": "Log in",
             "status": "Green", "checks": {"not_met": []}}]

    outcome = execution_outcome(scope, details, runs)

    names = [case.get("name") for case in ET.fromstring(report.junit(outcome)).iter("testcase")]
    assert names == ["Reset a password [e1]", "Reset a password [e2]", "Log in"]
    assert [c.name for c in outcome.failures] == ["Reset a password [e1]"]


def test_a_check_with_no_verdict_fails_a_strict_gate_on_an_inconclusive_batch():
    checks = [_check("A", "pass"), _check("B", None)]

    strict = batch_outcome(_batch("Inconclusive", {"pass": 1, "none": 1}, checks),
                           ("fail", "inconclusive"))
    lenient = batch_outcome(_batch("Inconclusive", {"pass": 1, "none": 1}, checks))

    assert [(c.name, c.outcome) for c in strict.cases] == [("A", "passed"), ("B", "failed")]
    assert [(c.name, c.outcome) for c in lenient.cases] == [("A", "passed"), ("B", "skipped")]


def test_fail_on_inconclusive_alone_still_fails_on_fail():
    outcome = batch_outcome(_batch("Failed", {"fail": 1}, [_check("A", "fail")]),
                            ("inconclusive",))
    assert outcome.fail_on == ("fail", "inconclusive")
    assert [(c.name, c.outcome) for c in outcome.failures] == [("A", "failed")]


def test_characters_xml_cant_hold_are_left_out_of_junit():
    scope = {"kind": "test-set", "id": "s", "name": "Smoke"}
    details = {"id": "x", "run_count": 1, "runs": {"NotRan": 1}}
    runs = [{"id": "r", "test_name": "a\x07", "status": "NotRan", "checks": None,
             "error": "The application said \x1b[31mno\x00"}]

    junit = report.junit(execution_outcome(scope, details, runs))

    error = ET.fromstring(junit).find(".//testcase/error")
    assert error.get("message") == "Not Ran: The application said [31mno"
    assert ET.fromstring(junit).find(".//testcase").get("name") == "a"


SCOPE = {"kind": "test-set", "id": "s", "name": "Smoke"}


def test_a_run_status_the_cli_doesnt_know_never_passes():
    details = {"id": "x", "run_count": 2, "runs": {"Green": 1, "Paused": 1}}
    runs = [{"id": "r1", "test_name": "a", "status": "Green", "checks": {"not_met": []}},
            {"id": "r2", "test_name": "b", "status": "Paused", "checks": None}]

    outcome = execution_outcome(SCOPE, details, runs, Policy(fail_on=("Red",)))

    assert not outcome.passed
    assert "a status this CLI doesn't know (Paused)" in outcome.reason
    assert [(c.name, c.outcome) for c in outcome.failures] == [("b", "error")]
    assert outcome.to_json()["reason"] == outcome.reason
    assert "fails on:" not in report.text(outcome)  # --fail-on didn't decide it


@pytest.mark.parametrize(("name", "heading"), [
    ("Smoke", "`Smoke`"),
    ("Smoke `v2` | prod", "``Smoke `v2` | prod``"),
    ("`edge`", "`` `edge` ``"),
    ("two\r\nlines", "`two lines`"),
])
def test_a_scopes_name_never_breaks_the_markdown_heading(name, heading):
    scope = {**SCOPE, "name": name}
    details = {"id": "x", "run_count": 1, "runs": {"Green": 1}}
    runs = [{"id": "r1", "test_name": "a\nb", "status": "Green", "checks": {"not_met": []}}]

    summary = report.markdown(execution_outcome(scope, details, runs))

    assert summary.splitlines()[0] == f"### ✅ Assay: test set {heading}"


def test_a_name_with_a_line_break_stays_on_its_line_of_the_text_report():
    details = {"id": "x", "run_count": 1, "runs": {"Red": 1}}
    runs = [{"id": "r1", "test_name": "two\nlines", "status": "Red",
             "checks": {"not_met": ["A\rB"]}}]

    lines = report.text(execution_outcome(SCOPE, details, runs)).splitlines()

    assert "  ✗ two lines: Red: not met: A B" in lines


def test_a_standalone_run_with_a_status_the_cli_doesnt_know_never_passes():
    scope = {"kind": "test", "id": "t", "name": "lonely"}

    outcome = run_outcome(scope, {"id": "r", "status": "Paused"}, Policy(fail_on=("Red",)))

    assert not outcome.passed and "(Paused)" in outcome.reason


def test_a_batch_status_the_cli_doesnt_know_never_passes():
    batch = _batch("Archived", {"pass": 1}, [_check("A", "pass")])

    assert batch_finished(batch)  # not in progress, so the wait ends
    outcome = batch_outcome(batch)
    assert not outcome.passed
    assert [(c.name, c.outcome) for c in outcome.failures] == [("binomial_gate", "error")]
    assert "(Archived)" in outcome.reason


def test_a_batch_is_in_progress_only_while_pending_or_running():
    for status in ("Pending", "Running"):
        assert not batch_finished(_batch(status))


# --- tolerance: judging a run or an execution by a policy ---

def _runs(*statuses, test_set="Smoke", not_met=()):
    return [Judged(test_set, status, tuple(not_met)) for status in statuses]


def _broken(rules):
    return [rule.text for rule in rules if rule.broken]


def test_by_default_any_failing_run_fails_the_gate():
    rules = judge(_runs("Green", "Amber"), STRICT)

    assert _broken(rules) == ["1 failure, none allowed"]


def test_max_failures_tolerates_up_to_that_many():
    policy = Policy(limits=Limits(max_failures=3))

    assert _broken(judge(_runs("Green", "Red", "Amber", "NotRan"), policy)) == []
    assert _broken(judge(_runs("Red", "Red", "Amber", "NotRan"), policy)) == [
        "4 failures > max 3"]


def test_min_pass_rate_counts_passing_runs_against_all_of_them():
    policy = Policy(limits=Limits(min_pass_rate=0.8))

    assert _broken(judge(_runs(*["Green"] * 8, "Red", "Red"), policy)) == []
    assert _broken(judge(_runs(*["Green"] * 7, "Red", "Red", "Red"), policy)) == [
        "7 of 10 passed (70%) < min 80%"]


def test_a_test_sets_own_limits_govern_it_and_every_other_run_must_pass():
    policy = Policy(test_sets={"Smoke": Limits(max_failures=0),
                               "Tone": Limits(min_pass_rate=0.5)})
    runs = (_runs("Green", test_set="Smoke") + _runs("Amber", "Green", test_set="Tone")
            + _runs("Red", test_set="Other"))

    assert _broken(judge(runs, policy)) == ["1 failure outside Smoke, Tone, none allowed"]
    assert [rule.name for rule in judge(runs, policy)] == [
        "Smoke: at most 0 failures", "Tone: a pass rate of at least 50%",
        "no failure outside Smoke, Tone"]


def test_with_a_limit_over_the_whole_a_set_without_its_own_isnt_limited_alone():
    policy = Policy(limits=Limits(max_failures=2), test_sets={"Smoke": Limits(max_failures=0)})
    runs = _runs("Red", "Red", test_set="Tone") + _runs("Green", test_set="Smoke")

    assert _broken(judge(runs, policy)) == []
    assert _broken(judge(runs + _runs("Amber", test_set="Smoke"), policy)) == [
        "Smoke: 1 failure > max 0", "3 failures > max 2"]


def test_a_blocking_check_fails_the_gate_even_in_a_run_otherwise_tolerated():
    policy = Policy(fail_on=("Red", "NotRan"), blocking_checks=("Toxicity",))
    details = {"id": "x", "run_count": 2, "runs": {"Green": 1, "Amber": 1}}
    runs = [{"id": "r1", "test_name": "a", "status": "Green", "checks": {"not_met": []}},
            {"id": "r2", "test_name": "b", "status": "Amber",
             "checks": {"not_met": ["Tone", "Toxicity"]}}]

    outcome = execution_outcome(SCOPE, details, runs, policy)

    assert not outcome.passed and outcome.judged
    assert [rule.text for rule in outcome.deciding] == ["blocking check 'Toxicity' failed in 1 run"]
    assert [(c.name, c.outcome, c.message) for c in outcome.failures] == [
        ("b", "failed", "Amber: not met: Tone, Toxicity (blocking: Toxicity)")]


def test_a_run_still_in_progress_when_the_runs_are_read_never_passes():
    details = {"id": "x", "run_count": 2, "runs": {"Green": 2}}
    runs = [{"id": "r1", "test_name": "a", "status": "Green", "checks": {"not_met": []}},
            {"id": "r2", "test_name": "b", "status": "Running", "checks": None}]

    outcome = execution_outcome(SCOPE, details, runs)

    assert not outcome.passed and outcome.reason == "1 run still in progress when its runs " \
                                                    "were read"
    assert [(c.name, c.outcome, c.message) for c in outcome.failures] == [
        ("b", "error", "Running: not finished when read")]


def test_a_blocking_check_is_matched_ignoring_letter_case():
    policy = Policy(fail_on=("Red", "NotRan"), blocking_checks=("toxicity",))
    details = {"id": "x", "run_count": 1, "runs": {"Amber": 1}}
    runs = [{"id": "r1", "test_name": "b", "status": "Amber",
             "checks": {"not_met": ["Toxicity"]}}]

    outcome = execution_outcome(SCOPE, details, runs, policy)

    assert [rule.text for rule in outcome.deciding] == ["blocking check 'toxicity' failed in 1 run"]
    assert outcome.failures[0].message == "Amber: not met: Toxicity (blocking: Toxicity)"


def test_a_blocking_check_the_scope_doesnt_ask_is_refused():
    policy = Policy(blocking_checks=("toxicity", "Valid JSON", "Tone"))

    with pytest.raises(PolicyError) as raised:
        check_blocking_checks(policy, ["Toxicity", "tone", "Bias", "Tone"], "test plan")
    assert str(raised.value) == ("the policy's blocking check 'Valid JSON' is no check this "
                                 "test plan asks (its checks: 'Bias', 'tone', 'Toxicity')")
    check_blocking_checks(Policy(blocking_checks=("TOXICITY",)), ["Toxicity"], "test")


@pytest.mark.parametrize(("statuses", "judged"), [
    (("NotRan", "NotRan"), False),  # the application didn't answer: nothing judged
    (("Green", "NotRan"), False),
    (("Amber", "NotRan"), True),  # a quality failure besides
])
def test_a_gate_failed_only_by_runs_that_not_ran_is_unjudged(statuses, judged):
    details = {"id": "x", "run_count": len(statuses), "runs": dict(Counter(statuses))}
    runs = [{"id": f"r{i}", "test_name": f"t{i}", "status": s, "checks": None}
            for i, s in enumerate(statuses)]

    outcome = execution_outcome(SCOPE, details, runs)

    assert not outcome.passed and outcome.judged is judged
    assert ("couldn't be judged" in report.text(outcome)) is not judged


def test_tolerated_failures_dont_hide_that_not_ran_alone_broke_the_limit():
    policy = Policy(limits=Limits(max_failures=2))
    details = {"id": "x", "run_count": 5, "runs": {"Amber": 2, "NotRan": 3}}
    runs = [{"id": f"r{i}", "test_name": f"t{i}", "status": s, "checks": None}
            for i, s in enumerate(["Amber", "Amber", "NotRan", "NotRan", "NotRan"])]

    outcome = execution_outcome(SCOPE, details, runs, policy)

    assert not outcome.passed and not outcome.judged  # 2 Ambers are within the limit


def test_a_batch_that_couldnt_decide_anything_is_unjudged():
    assert not batch_outcome(_batch("NotRan")).judged
    assert batch_outcome(_batch("Incomplete")).judged  # stopped: a choice, not a failure


def test_a_policy_naming_a_test_set_the_scope_doesnt_run_is_refused():
    policy = Policy(test_sets={"Smok": Limits(max_failures=0)})

    with pytest.raises(PolicyError, match="names test set 'Smok', which this test plan "
                                          "doesn't run \\(it runs: 'Smoke', 'Tone'\\)"):
        check_test_sets(policy, ["Tone", "Smoke"], "test plan")


PLAN = {"kind": "test-plan", "id": "p", "name": "Release"}


def _plan_outcome(policy):
    details = {"id": "x", "run_count": 3, "runs": {"Green": 2, "Red": 1}}
    runs = [{"id": "r1", "test_name": "Login", "status": "Green", "test_set_name": "Smoke",
             "test_set_entry_id": {"id": "e1"}, "checks": {"not_met": []}},
            {"id": "r2", "test_name": "Login", "status": "Red", "test_set_name": "Tone",
             "test_set_entry_id": {"id": "e2"}, "checks": {"not_met": ["Polite"]}},
            {"id": "r3", "test_name": "Refund", "status": "Green", "test_set_name": "Tone",
             "test_set_entry_id": {"id": "e3"}, "checks": {"not_met": []}}]
    return execution_outcome(PLAN, details, runs, policy)


def test_a_plans_runs_are_grouped_and_judged_by_their_test_set():
    outcome = _plan_outcome(Policy(test_sets={"Smoke": Limits(max_failures=0),
                                              "Tone": Limits(min_pass_rate=0.5)}))

    assert outcome.passed
    assert report.text(outcome).splitlines()[2] == "  gate: Tone: 1 of 2 passed (50%) ≥ min 50%"
    suites = ET.fromstring(report.junit(outcome)).findall("testsuite")
    # Tone's Red run was tolerated: a CI system reading the report mustn't fail on it
    assert [(s.get("name"), s.get("tests"), s.get("failures")) for s in suites] == [
        ("Smoke", "1", "0"), ("Tone", "2", "0"), ("Assay gate", "2", "0")]
    assert [c.find("system-out").text for c in suites[1].iter("testcase")
            if c.get("name") == "Login"] == ["Red: not met: Polite (tolerated by the gate)"]
    # the same test in two sets: told apart by its set, no id needed
    assert [c.get("name") for c in suites[0].iter("testcase")] == ["Login"]


def _set_outcome(statuses, policy, not_met=None):
    details = {"id": "x", "run_count": len(statuses), "runs": dict(Counter(statuses))}
    runs = [{"id": f"r{i}", "test_name": f"t{i}", "status": status,
             "test_set_name": "Tone" if i % 2 else "Smoke",
             "checks": {"not_met": (not_met or {}).get(i, ["A"] if status != "Green" else [])}}
            for i, status in enumerate(statuses)]
    return execution_outcome(PLAN, details, runs, policy)


def _junit_outcomes(outcome):
    return {case.get("name"): [child.tag for child in case]
            for case in ET.fromstring(report.junit(outcome)).iter("testcase")
            if case.get("classname") != report.GATE_SUITE}


def test_runs_a_limit_tolerated_are_reported_passed_and_the_others_failed():
    # t0, t2 in Smoke (no failure allowed); t1, t3 in Tone (one allowed)
    outcome = _set_outcome(["Red", "Red", "Green", "Green"],
                           Policy(test_sets={"Smoke": Limits(max_failures=0),
                                             "Tone": Limits(max_failures=1)}))

    assert not outcome.passed  # Smoke broke its limit
    assert _junit_outcomes(outcome) == {"t0": ["failure"], "t1": ["system-out"],
                                        "t2": [], "t3": []}
    assert [case.name for case in outcome.failures] == ["t0"]


def test_a_run_counted_by_a_broken_limit_over_the_whole_is_never_tolerated():
    outcome = _set_outcome(["Red", "Red", "Green"],
                           Policy(limits=Limits(max_failures=1),
                                  test_sets={"Tone": Limits(max_failures=5)}))

    assert not outcome.passed
    # t1 is within Tone's limit, but the limit over the whole counts it too
    assert _junit_outcomes(outcome) == {"t0": ["failure"], "t1": ["failure"], "t2": []}


def test_a_blocking_check_failing_is_never_tolerated():
    outcome = _set_outcome(["Green", "Red"], Policy(limits=Limits(max_failures=5),
                                                    blocking_checks=("Toxicity",)),
                           not_met={1: ["Toxicity"]})

    assert _junit_outcomes(outcome)["t1"] == ["failure"]


def test_a_strict_gate_tolerates_nothing():
    outcome = _set_outcome(["Green", "Amber"], STRICT)

    assert _junit_outcomes(outcome)["t1"] == ["failure"]


def test_a_standalone_run_a_limit_tolerated_is_reported_passed():
    scope = {"kind": "test", "id": "t", "name": "lonely"}
    details = {"id": "r", "status": "Red", "results": {"A": {"passed": False}}}

    outcome = run_outcome(scope, details, Policy(limits=Limits(max_failures=1)))

    assert outcome.passed and outcome.failures == []
    assert _junit_outcomes(outcome) == {"lonely": ["system-out"]}


@pytest.mark.parametrize(("passed", "total", "limit", "text"), [
    (1899, 2000, 0.95, "1899 of 2000 passed (94.95%) < min 95%"),
    (19, 20, 0.95, "19 of 20 passed (95%) ≥ min 95%"),
    (2, 3, 0.9, "2 of 3 passed (66.7%) < min 90%"),
    (9999, 10000, 0.99995, "9999 of 10000 passed (99.99%) < min 99.995%"),
])
def test_a_pass_rate_is_shown_with_the_digits_that_tell_it_from_the_limit(
        passed, total, limit, text):
    runs = [Judged(None, "Green")] * passed + [Judged(None, "Red")] * (total - passed)

    (rule,) = judge(runs, Policy(limits=Limits(min_pass_rate=limit)))

    assert rule.text == text


def test_runs_listed_that_dont_match_the_executions_counts_never_pass():
    details = {"id": "x", "run_count": 3, "runs": {"Green": 2, "Red": 1}}
    runs = [{"id": f"r{i}", "test_name": f"t{i}", "status": "Green", "checks": {"not_met": []}}
            for i in range(2)]  # the Red run's page lost

    outcome = execution_outcome(SCOPE, details, runs)

    assert not outcome.passed
    assert outcome.reason == ("its runs as listed (2 Green) don't match its counts "
                              "(3 runs: 2 Green, 1 Red)")


def test_a_broken_rule_is_named_in_every_output():
    outcome = _plan_outcome(Policy(test_sets={"Tone": Limits(max_failures=0)}))

    assert not outcome.passed
    assert "  gate: Tone: 1 failure > max 0" in report.text(outcome)
    assert "  ✗ Tone › Login: Red: not met: Polite" in report.text(outcome)
    assert "**Gate:** Tone: 1 failure > max 0" in report.markdown(outcome)
    gate = ET.fromstring(report.junit(outcome)).find("testsuite[@name='Assay gate']")
    failure = gate.find("testcase[@name='Tone: at most 0 failures']/failure")
    assert failure.get("message") == "Tone: 1 failure > max 0"
    (rule,) = [r for r in outcome.to_json()["gate"] if r["broken"]]
    assert rule == {"rule": "max_failures", "test_set": "Tone", "limit": 0, "actual": 1,
                    "broken": True, "text": "Tone: 1 failure > max 0"}


def test_the_default_policy_adds_no_gate_line_or_suite():
    outcome = _plan_outcome(STRICT)

    assert "gate:" not in report.text(outcome)
    assert "fails on: Amber, Red, NotRan" in report.text(outcome)
    names = [s.get("name") for s in ET.fromstring(report.junit(outcome)).findall("testsuite")]
    assert "Assay gate" not in names
