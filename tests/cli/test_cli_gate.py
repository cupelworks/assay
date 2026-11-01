# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The gate's decisions on the API's replies, and the reports written from them:
the cases a real batch is slow or awkward to bring about."""
from xml.etree import ElementTree as ET

import pytest

from assay_cli import report
from assay_cli.gate import batch_finished, batch_outcome, execution_outcome, run_outcome


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

    outcome = execution_outcome(SCOPE, details, runs, ("Red",))

    assert not outcome.passed
    assert "a status this CLI doesn't know (Paused)" in outcome.reason
    assert [(c.name, c.outcome) for c in outcome.failures] == [("b", "error")]
    assert outcome.to_json()["reason"] == outcome.reason
    assert "fails on:" not in report.text(outcome)  # --fail-on didn't decide it


def test_a_standalone_run_with_a_status_the_cli_doesnt_know_never_passes():
    scope = {"kind": "test", "id": "t", "name": "lonely"}

    outcome = run_outcome(scope, {"id": "r", "status": "Paused"}, ())

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
