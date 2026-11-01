# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The CLI against the real API in-process: the `db` fixture's client is an
httpx.Client, so the CLI talks to the same routes a pipeline would. Between
two polls, the "sleep" executes the dispatched runs as a worker would."""
import io
import json
import os
import signal
import sys
from xml.etree import ElementTree as ET

import pytest

from assay_cli.cli import (
    EXIT_CANCELLED,
    EXIT_ERROR,
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_PASSED,
    EXIT_TIMED_OUT,
    _cancel,
    main,
)
from assay_cli.client import ApiError, AssayClient

# the in-process client ignores each request's own timeout, which the waits pass
pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument:DeprecationWarning")

PASSES = {"name": "Contains", "config": {"substring": "answer"}}
MISSES = {"name": "Contains", "label": "Misses", "config": {"substring": "absent"}}


class Cli:
    def __init__(self, db):
        self.db = db
        self.out, self.err = io.StringIO(), io.StringIO()
        self.now = 0.0
        self.sleeps = 0
        self.worker_running = True
        self.on_sleep = None

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds
        if self.worker_running:
            pending, self.db.dispatched[:] = list(self.db.dispatched), []
            self.db.execute(pending)
        if self.on_sleep:
            self.on_sleep()

    def __call__(self, *argv, env=None, client=None):
        self.out, self.err = io.StringIO(), io.StringIO()
        return main(list(argv), client=client or AssayClient(self.db.client), env=env or {},
                    out=self.out, err=self.err, sleep=self.sleep, clock=lambda: self.now)


class Flaky(AssayClient):
    """Fails the requests `fails(method, path)` picks with `error`, `times` times."""

    def __init__(self, http, fails, error, times=1):
        super().__init__(http)
        self.fails, self.error, self.times = fails, error, times

    def _request(self, method, path, **kwargs):
        if self.times and self.fails(method, path):
            self.times -= 1
            raise self.error
        return super()._request(method, path, **kwargs)


def _reading_an_execution(method, path):
    return method == "GET" and "/executions/" in path


GATEWAY_DOWN = ApiError("GET …: 502 Bad Gateway", 502, transient=True)


@pytest.fixture
def cli(db):
    return Cli(db)


def test_a_test_set_that_passes_exits_0(db, cli, tmp_path):
    db.test_set("Smoke", [db.test(name="a"), db.test(name="b")])
    junit = tmp_path / "out" / "assay.xml"

    code = cli("run", "test-set", "Smoke", "--junit", str(junit), "--interval", "1")

    assert code == EXIT_PASSED, cli.err.getvalue()
    assert cli.out.getvalue().startswith("PASSED: test set 'Smoke', 2 runs: 2 Green")
    assert "Started execution" in cli.err.getvalue()
    assert "2/2 runs finished (2 Green)" in cli.err.getvalue()
    suite = ET.parse(junit).getroot().find("testsuite")
    assert (suite.get("name"), suite.get("tests"), suite.get("failures")) == ("Smoke", "2", "0")
    assert sorted(case.get("name") for case in suite.iter("testcase")) == ["a", "b"]


def test_a_failing_check_fails_the_step_and_names_it(db, cli, tmp_path):
    db.test_set("Smoke", [db.test(name="good"), db.test(name="bad", checks=[PASSES, MISSES])])
    summary = tmp_path / "summary.md"

    code = cli("run", "test-set", "Smoke", "--junit", str(tmp_path / "j.xml"),
               env={"GITHUB_STEP_SUMMARY": str(summary)})

    assert code == EXIT_FAILED
    out = cli.out.getvalue()
    assert out.startswith("FAILED: test set 'Smoke', 2 runs: 1 Green, 1 Amber, 0 Red, 0 NotRan")
    assert "✗ bad: Amber: not met: Misses" in out
    assert "❌ Assay: test set `Smoke`" in summary.read_text()
    failure = ET.parse(tmp_path / "j.xml").getroot().find(".//testcase[@name='bad']/failure")
    assert failure.get("message") == "Amber: not met: Misses"


def test_fail_on_can_tolerate_amber(db, cli):
    db.test_set("Smoke", [db.test(name="bad", checks=[PASSES, MISSES])])

    assert cli("run", "test-set", "Smoke", "--fail-on", "red,NotRan") == EXIT_PASSED
    assert "PASSED" in cli.out.getvalue()


def test_a_test_plan_by_id_with_json_output(db, cli):
    plan = db.test_plan("Release checks", [db.test_set("a", [db.test(name="one")]),
                                           db.test_set("b", [db.test(name="two")])])

    code = cli("run", "test-plan", plan["id"], "--json")

    assert code == EXIT_PASSED
    outcome = json.loads(cli.out.getvalue())
    assert outcome["kind"] == "execution" and outcome["passed"] is True
    assert outcome["scope"] == {"kind": "test-plan", "id": plan["id"], "name": "Release checks"}
    assert outcome["counts"]["Green"] == 2 and outcome["failures"] == []


def test_a_standalone_test(db, cli):
    db.test(name="lonely", checks=[MISSES])

    code = cli("run", "test", "lonely")

    assert code == EXIT_FAILED
    assert cli.out.getvalue().startswith("FAILED: test 'lonely': Red, 0/1 checks met")


def test_a_replay_reruns_the_same_entries(db, cli):
    test_set = db.test_set("Smoke", [db.test(name="a")])
    first = db.client.post(f"/runs/test-sets/{test_set['id']}").json()

    code = cli("run", "test-set", "Smoke", "--replay", first["id"], "--json")

    assert code == EXIT_PASSED
    replay = json.loads(cli.out.getvalue())["id"]
    details = db.client.get(f"/runs/test-sets/{test_set['id']}/executions/{replay}").json()
    assert details["replayed_execution_id"] == {"id": first["id"]}


def test_no_wait_prints_the_id_and_leaves_it_running(db, cli):
    db.test_set("Smoke", [db.test(name="a")])

    assert cli("run", "test-set", "Smoke", "--no-wait") == EXIT_PASSED
    assert cli.sleeps == 0 and len(db.dispatched) == 1
    assert cli.out.getvalue().strip()  # the execution's id


def test_without_a_worker_it_times_out(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    cli.worker_running = False

    code = cli("run", "test-set", "Smoke", "--timeout", "12", "--interval", "5")

    assert code == EXIT_TIMED_OUT
    assert "Still not finished after 12s: 0/1 runs finished" in cli.err.getvalue()


def test_unknown_and_ambiguous_names_are_errors(db, cli):
    db.test(name="Twin")
    db.test(name="Twin")
    db.test(name="Twin brother")

    assert cli("run", "test-set", "Nope") == EXIT_ERROR
    assert "No test set is named 'Nope'" in cli.err.getvalue()
    assert cli("run", "test", "Twin") == EXIT_ERROR
    assert "2 tests are named 'Twin'" in cli.err.getvalue()


def test_a_refused_run_is_an_error_with_the_apis_reason(db, cli):
    db.test_set("Empty", [])

    assert cli("run", "test-set", "Empty") == EXIT_ERROR
    assert "409 No Test Set Entries found" in cli.err.getvalue()


def test_a_batch_that_passes(db, cli, tmp_path):
    db.test_set("Smoke", [db.test(name="a")])

    code = cli("batch", "test-set", "Smoke", "-s", "binomial_gate", "--note", "PR 7",
               "--junit", str(tmp_path / "j.xml"))

    assert code == EXIT_PASSED, cli.out.getvalue() + cli.err.getvalue()
    assert cli.out.getvalue().startswith(
        "PASSED: batch of test set 'Smoke' (binomial_gate): Passed.")
    case = ET.parse(tmp_path / "j.xml").getroot().find(".//testcase")
    assert (case.get("name"), case.get("classname")) == ("Contains", "Smoke / a")
    batch_id = json.loads(db.client.get("/statistics/batches").text)["items"][0]
    assert batch_id["note"] == "PR 7"


def test_a_batch_that_fails(db, cli):
    db.test_set("Smoke", [db.test(name="a", checks=[PASSES, MISSES])])

    code = cli("batch", "test-set", "Smoke", "-s", "binomial_gate", "--json")

    assert code == EXIT_FAILED
    outcome = json.loads(cli.out.getvalue())
    assert outcome["status"] == "Failed" and outcome["counts"]["fail"] == 1
    assert [f["name"] for f in outcome["failures"]] == ["Misses"]


def test_a_batch_left_running_can_be_stopped_on_timeout(db, cli):
    test = db.test(name="a")
    cli.worker_running = False

    code = cli("batch", "test", test["id"], "-s", "binomial_gate", "--timeout", "1",
               "--stop-on-timeout")

    assert code == EXIT_TIMED_OUT
    assert "Stopped batch" in cli.err.getvalue()
    batch = db.client.get("/statistics/batches").json()["items"][0]
    assert batch["stopped_at"] is not None


def test_a_bad_parameter_is_the_apis_422(db, cli):
    db.test(name="a")

    assert cli("batch", "test", "a", "-s", "binomial_gate", "-p", "target=7") == EXIT_ERROR
    assert "422" in cli.err.getvalue()


def test_health_is_a_failure_until_a_worker_is_ready(cli):
    code = cli("health")

    assert code == EXIT_FAILED  # no broker in the test suite
    assert cli.out.getvalue().startswith("Assay ")
    assert "ready" not in cli.out.getvalue()


def test_a_run_with_no_application_to_ask_is_not_ran(db, cli):
    db.test_set("Live", [db.test(name="asks", model_output=None)])

    code = cli("run", "test-set", "Live", "--fail-on", "Red")
    assert code == EXIT_PASSED  # NotRan tolerated on request

    assert cli("run", "test-set", "Live") == EXIT_FAILED
    assert "! asks: Not Ran:" in cli.out.getvalue()


def test_a_gateways_passing_failure_while_waiting_is_tried_again(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    client = Flaky(db.client, _reading_an_execution, GATEWAY_DOWN, times=2)

    code = cli("run", "test-set", "Smoke", "--interval", "1", client=client)

    assert code == EXIT_PASSED, cli.err.getvalue()
    assert cli.err.getvalue().count("502 Bad Gateway; trying again") == 2


def test_a_gateway_down_until_the_deadline_times_out(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    client = Flaky(db.client, _reading_an_execution, GATEWAY_DOWN, times=1000)

    code = cli("run", "test-set", "Smoke", "--timeout", "10", "--interval", "5", client=client)

    assert code == EXIT_TIMED_OUT
    assert "the last check failed (GET …: 502 Bad Gateway)" in cli.err.getvalue()


def test_the_apis_own_5xx_while_waiting_is_tried_again(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    client = Flaky(db.client, _reading_an_execution, ApiError("GET …: 500 Locked", 500,
                                                                         transient=True))

    assert cli("run", "test-set", "Smoke", client=client) == EXIT_PASSED
    assert "500 Locked; trying again" in cli.err.getvalue()


def test_a_refusal_while_waiting_is_not_tried_again(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    client = Flaky(db.client, _reading_an_execution, ApiError("GET …: 401 Expired", 401))

    assert cli("run", "test-set", "Smoke", client=client) == EXIT_ERROR
    assert "trying again" not in cli.err.getvalue()


def test_the_wait_never_sleeps_past_the_timeout(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    cli.worker_running = False

    assert cli("run", "test-set", "Smoke", "--timeout", "7", "--interval", "5") == EXIT_TIMED_OUT
    assert cli.now == 7


def _reading_the_runs(method, path):
    return method == "GET" and path.endswith("/test-runs")


def test_reading_a_finished_executions_runs_has_its_own_time(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    client = Flaky(db.client, _reading_the_runs, GATEWAY_DOWN)

    # it finishes on the check that comes at the deadline
    code = cli("run", "test-set", "Smoke", "--timeout", "1", "--interval", "5", client=client)

    assert code == EXIT_PASSED, cli.err.getvalue()


def test_runs_that_cant_be_read_after_it_finished_are_an_error(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    client = Flaky(db.client, _reading_the_runs, GATEWAY_DOWN, times=1000)

    assert cli("run", "test-set", "Smoke", client=client) == EXIT_ERROR
    assert "It finished, but its runs couldn't be read: GET …: 502" in cli.err.getvalue()


def test_an_error_while_waiting_stops_the_batch(db, cli):
    test = db.test(name="a")
    cli.worker_running = False
    client = Flaky(db.client, lambda method, path: method == "GET" and "/batches/" in path,
                   ApiError("GET …: 401 Expired", 401))

    code = cli("batch", "test", test["id"], "-s", "binomial_gate", "--stop-on-timeout",
               client=client)

    assert code == EXIT_ERROR
    assert "Stopped batch" in cli.err.getvalue()
    assert db.client.get("/statistics/batches").json()["items"][0]["stopped_at"] is not None


def _cancel_the_job():
    os.kill(os.getpid(), signal.SIGTERM)


def _press_ctrl_c():
    raise KeyboardInterrupt


@pytest.mark.parametrize(("interrupt", "exit_code"), [(_cancel_the_job, EXIT_CANCELLED),
                                                      (_press_ctrl_c, EXIT_INTERRUPTED)])
def test_a_cancelled_job_stops_its_batch(db, cli, interrupt, exit_code):
    test = db.test(name="a")
    cli.worker_running = False
    cli.on_sleep = interrupt

    code = cli("batch", "test", test["id"], "-s", "binomial_gate", "--stop-on-timeout")

    assert code == exit_code
    assert "Stopped batch" in cli.err.getvalue()
    assert db.client.get("/statistics/batches").json()["items"][0]["stopped_at"] is not None
    assert signal.getsignal(signal.SIGTERM) is not _cancel  # main put the old one back


def test_a_failed_stop_keeps_the_timeout(db, cli):
    test = db.test(name="a")
    cli.worker_running = False
    client = Flaky(db.client, lambda method, path: path.endswith("/stop"),
                   ApiError("POST …/stop: 409 Already finished", 409))

    code = cli("batch", "test", test["id"], "-s", "binomial_gate", "--timeout", "1",
               "--stop-on-timeout", client=client)

    assert code == EXIT_TIMED_OUT
    assert "Couldn't stop batch" in cli.err.getvalue()


def test_the_markdown_summary_creates_its_folder_and_is_written_once(db, cli, tmp_path):
    db.test_set("Smoke", [db.test(name="a")])
    summary = tmp_path / "reports" / "md" / "summary.md"

    code = cli("run", "test-set", "Smoke", "--markdown", str(summary),
               env={"GITHUB_STEP_SUMMARY": str(summary)})

    assert code == EXIT_PASSED, cli.err.getvalue()
    assert summary.read_text().count("### ✅ Assay: test set `Smoke`") == 1


class Limits(AssayClient):
    """Records how long each request may wait."""

    def __init__(self, http):
        super().__init__(http)
        self.limits = []

    def _request(self, method, path, **kwargs):
        self.limits.append((method, self._limit))
        return super()._request(method, path, **kwargs)


def test_no_check_waits_past_the_time_left(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    cli.worker_running = False
    client = Limits(db.client)

    code = cli("run", "test-set", "Smoke", "--timeout", "7", "--interval", "5", client=client)

    assert code == EXIT_TIMED_OUT
    reads = [limit for method, limit in client.limits if method == "GET"]
    # at 0s, 7s left; at 5s, 2s left, but a check gets 5s; at 7s, the last one, 5s
    assert reads[-3:] == [7, 5, 5]
    assert ("POST", None) in client.limits  # starting it isn't limited


class CancelOn(io.StringIO):
    """The job cancelled while the line naming `text` is written."""

    def __init__(self, text):
        super().__init__()
        self.text = text

    def write(self, line):
        if self.text and self.text in line:
            self.text = None
            raise KeyboardInterrupt
        return super().write(line)


def test_a_cancel_just_after_the_batch_starts_still_stops_it(db):
    test = db.test(name="a")
    err = CancelOn("Started batch")

    code = main(["batch", "test", test["id"], "-s", "binomial_gate", "--stop-on-timeout"],
                client=AssayClient(db.client), env={}, out=io.StringIO(), err=err,
                sleep=lambda seconds: None, clock=lambda: 0.0)

    assert code == EXIT_INTERRUPTED
    assert "Stopped batch" in err.getvalue()
    assert db.client.get("/statistics/batches").json()["items"][0]["stopped_at"] is not None


def test_a_report_prints_on_a_pipe_that_isnt_utf8(db, cli, monkeypatch, tmp_path):
    db.test_set("Smoke", [db.test(name="bad ✗ naïve", checks=[MISSES])])
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    junit = tmp_path / "j.xml"

    code = main(["run", "test-set", "Smoke", "--junit", str(junit)],
                client=AssayClient(db.client), env={}, sleep=cli.sleep, clock=lambda: cli.now)

    assert code == EXIT_FAILED
    stdout.flush()
    assert "✗ bad ✗ naïve: Red" in stdout.buffer.getvalue().decode("utf-8")
    assert junit.exists()
