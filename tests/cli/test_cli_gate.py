# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The gate's decisions on the API's replies, and the command line's parsing:
the cases a real batch is slow or awkward to bring about."""
import io
from xml.etree import ElementTree as ET

import pytest

from assay_cli import report
from assay_cli.cli import EXIT_ERROR, _headers, build_parser, main
from assay_cli.client import ApiError
from assay_cli.gate import batch_finished, batch_outcome, execution_outcome


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


def test_headers_from_the_environment_and_flags():
    args = build_parser().parse_args(["--header", "X-A: 1", "health", "--header", "X-B:2"])
    headers = _headers(args, {"ASSAY_API_HEADERS": '{"X-C": "3", "X-A": "0"}'})
    assert headers == {"X-A": "1", "X-B": "2", "X-C": "3"}
    with pytest.raises(ApiError):
        _headers(build_parser().parse_args(["health"]), {"ASSAY_API_HEADERS": "[1]"})


def test_connection_options_before_the_command_survive_the_subcommand():
    args = build_parser().parse_args(["--url", "http://a", "run", "test", "x"])
    assert args.url == "http://a" and args.header == [] and args.http_timeout == 30.0


def test_fail_on_is_checked_and_normalised():
    args = build_parser().parse_args(["run", "test", "x", "--fail-on", "red, notran,Red"])
    assert args.fail_on == ("Red", "NotRan")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "test", "x", "--fail-on", "Blue"])


def test_without_a_url_it_says_how_to_give_one():
    err = io.StringIO()
    assert main(["health"], env={}, out=io.StringIO(), err=err) == EXIT_ERROR
    assert "--url or $ASSAY_API_URL" in err.getvalue()
