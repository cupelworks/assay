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
    EXIT_UNJUDGED,
    READ_SECONDS,
    STOP_SECONDS,
    Cancelled,
    _cancel,
    main,
)
from assay_cli.client import UNTIL_DEADLINE, ApiError, AssayClient

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


class Failures(AssayClient):
    """Fails the requests `fails(method, path)` picks with each of `errors`, in turn."""

    def __init__(self, http, fails, errors):
        super().__init__(http)
        self.fails, self.errors = fails, list(errors)

    def _request(self, method, path, **kwargs):
        if self.errors and self.fails(method, path):
            raise self.errors.pop(0)
        return super()._request(method, path, **kwargs)


def _reading_an_execution(method, path):
    return method == "GET" and "/executions/" in path


GATEWAY_DOWN = ApiError("GET …: 502 Bad Gateway", 502, retries=UNTIL_DEADLINE)


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

    assert cli("run", "test-set", "Live") == EXIT_UNJUDGED  # nothing about quality decided
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
                                                                         retries=3))

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

    def _request(self, method, path, time_left=None, **kwargs):
        self.limits.append((method, path, time_left and time_left()))
        return super()._request(method, path, time_left=time_left, **kwargs)


def test_no_check_waits_past_the_time_left(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    cli.worker_running = False
    client = Limits(db.client)

    code = cli("run", "test-set", "Smoke", "--timeout", "7", "--interval", "5", client=client)

    assert code == EXIT_TIMED_OUT
    reads = [limit for method, _, limit in client.limits if method == "GET"]
    # at 0s, 7s left; at 5s, 2s left, but a check gets 5s; at 7s, the last one, 5s
    assert reads[-3:] == [7, 5, 5]
    assert [limit for method, _, limit in client.limits if method == "POST"] == [None]


def test_stopping_a_batch_is_quick(db, cli):
    test = db.test(name="a")
    cli.worker_running = False
    client = Limits(db.client)

    code = cli("batch", "test", test["id"], "-s", "binomial_gate", "--timeout", "1",
               "--stop-on-timeout", client=client)

    assert code == EXIT_TIMED_OUT
    stops = [limit for _, path, limit in client.limits if path.endswith("/stop")]
    assert stops == [STOP_SECONDS]


def test_a_second_ctrl_c_while_stopping_keeps_the_steps_own_ending(db, cli):
    test = db.test(name="a")
    cli.worker_running = False
    client = Flaky(db.client, lambda method, path: path.endswith("/stop"), KeyboardInterrupt())

    code = cli("batch", "test", test["id"], "-s", "binomial_gate", "--timeout", "1",
               "--stop-on-timeout", client=client)

    assert code == EXIT_TIMED_OUT
    assert "Couldn't stop batch" in cli.err.getvalue()
    assert "(interrupted)" in cli.err.getvalue()


def test_a_steady_500_ends_the_wait_after_three_tries(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    client = Flaky(db.client, _reading_an_execution,
                   ApiError("GET …: 500 A bug", 500, retries=3), times=1000)

    assert cli("run", "test-set", "Smoke", "--timeout", "0", client=client) == EXIT_ERROR
    assert cli.err.getvalue().count("500 A bug; trying again") == 3


def test_a_report_that_cant_be_written_keeps_the_outcome(db, cli, tmp_path):
    db.test_set("Smoke", [db.test(name="bad", checks=[MISSES])])
    summary = tmp_path / "summary.md"

    # a folder can't be written as a file
    code = cli("run", "test-set", "Smoke", "--junit", str(tmp_path),
               env={"GITHUB_STEP_SUMMARY": str(summary)})

    assert code == EXIT_ERROR
    assert cli.out.getvalue().startswith("FAILED: test set 'Smoke'")
    assert f"couldn't write {tmp_path}:" in cli.err.getvalue()
    assert "JUnit report" not in cli.err.getvalue()
    assert "❌ Assay: test set `Smoke`" in summary.read_text()


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


def _server_error():
    return ApiError("GET …: 500 Warming up", 500, retries=3)


def test_gateway_failures_dont_use_up_a_500s_tries(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    # the API restarting: a gateway's 502s, then its own 500s while it warms up
    errors = [GATEWAY_DOWN] * 4 + [_server_error() for _ in range(3)]
    client = Failures(db.client, _reading_an_execution, errors)

    code = cli("run", "test-set", "Smoke", "--interval", "1", client=client)

    assert code == EXIT_PASSED, cli.err.getvalue()
    assert cli.err.getvalue().count("500 Warming up; trying again") == 3


def test_a_fourth_500_ends_the_wait_whatever_gateway_failures_came_between(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    errors = [_server_error(), GATEWAY_DOWN] * 3 + [_server_error()]
    client = Failures(db.client, _reading_an_execution, errors)

    assert cli("run", "test-set", "Smoke", "--timeout", "0", client=client) == EXIT_ERROR
    assert cli.err.getvalue().count("500 Warming up; trying again") == 3


def test_a_test_named_like_an_id_is_found_by_its_name(db, cli):
    name = "3f0c2b1e-7d4a-4c1b-9e2f-0a1b2c3d4e5f"
    db.test(name=name)

    assert cli("run", "test", name) == EXIT_PASSED, cli.err.getvalue()
    assert cli("run", "test", "00000000-0000-4000-8000-000000000000") == EXIT_ERROR
    assert "No test has the id or the name '00000000-" in cli.err.getvalue()


def test_a_sigterm_while_stopping_waits_for_the_stop_then_cancels(db, cli):
    test = db.test(name="a")
    cli.worker_running = False

    class Stubborn(AssayClient):
        def _request(self, method, path, **kwargs):
            if path.endswith("/stop"):
                os.kill(os.getpid(), signal.SIGTERM)  # the CI system, impatient
            return super()._request(method, path, **kwargs)

    code = cli("batch", "test", test["id"], "-s", "binomial_gate", "--timeout", "1",
               "--stop-on-timeout", client=Stubborn(db.client))

    assert code == EXIT_CANCELLED  # the job was cancelled, whatever timed out before
    assert "Stopped batch" in cli.err.getvalue()
    assert db.client.get("/statistics/batches").json()["items"][0]["stopped_at"] is not None
    assert signal.getsignal(signal.SIGTERM) is not _cancel


def test_a_sigterm_while_cleaning_up_is_a_cancel_not_a_failed_gate(db, cli, monkeypatch):
    db.test_set("Smoke", [db.test(name="a")])
    client = AssayClient(db.client)

    def close():
        raise Cancelled("cancelled (SIGTERM)")

    monkeypatch.setattr(client, "close", close)
    monkeypatch.setattr(AssayClient, "connect", classmethod(lambda cls, *a, **k: client))
    err = io.StringIO()

    code = main(["--url", "http://assay", "run", "test-set", "Smoke"], env={},
                out=io.StringIO(), err=err, sleep=cli.sleep, clock=lambda: cli.now)

    assert code == EXIT_CANCELLED
    assert "assay: cancelled (SIGTERM)" in err.getvalue()


def _looking_up_a_set(method, path):
    return method == "GET" and path == "/test-sets"


def test_a_gateways_passing_failure_while_looking_it_up_is_tried_again(db, cli):
    db.test_set("Smoke", [db.test(name="a")])
    client = Flaky(db.client, _looking_up_a_set, GATEWAY_DOWN, times=2)

    code = cli("run", "test-set", "Smoke", "--interval", "1", client=client)

    assert code == EXIT_PASSED, cli.err.getvalue()
    assert cli.err.getvalue().count("502 Bad Gateway; trying again") == 2


def test_a_lookup_that_keeps_failing_is_an_error_naming_what_it_looked_for(db, cli):
    client = Flaky(db.client, _looking_up_a_set, GATEWAY_DOWN, times=1000)

    assert cli("run", "test-set", "Smoke", client=client) == EXIT_ERROR
    assert "Couldn't look up the test set 'Smoke': GET …: 502" in cli.err.getvalue()
    assert cli.now == READ_SECONDS


@pytest.mark.parametrize(("interrupt", "exit_code"), [
    (KeyboardInterrupt(), EXIT_INTERRUPTED), (Cancelled("cancelled (SIGTERM)"), EXIT_CANCELLED)])
def test_an_interrupt_while_starting_says_it_may_have_started(db, cli, interrupt, exit_code):
    db.test_set("Smoke", [db.test(name="a")])
    client = Flaky(db.client, lambda method, path: method == "POST", interrupt)

    assert cli("run", "test-set", "Smoke", client=client) == exit_code
    assert "Interrupted while starting the execution: it may have started" in cli.err.getvalue()


def test_anything_unforeseen_is_an_error_not_a_failed_gate():
    class Odd:
        def health(self, time_left=None):
            raise KeyError("runs")

    err = io.StringIO()
    assert main(["health"], client=Odd(), env={}, out=io.StringIO(), err=err) == EXIT_ERROR
    assert "unexpected error: KeyError: 'runs'" in err.getvalue()


# --- tolerance: a policy file and the flags over it ---

POLICY = """\
test_plan: "Release"
fail_on: [Red, NotRan]
test_sets:
  "Smoke": { max_failures: 0 }
  "Tone": { min_pass_rate: 0.5 }
"""


@pytest.fixture
def release(db):
    """A plan of two sets: Smoke all Green; Tone with one Red run out of two."""
    smoke = db.test_set("Smoke", [db.test(name="login")])
    tone = db.test_set("Tone", [db.test(name="polite", checks=[MISSES]),
                                db.test(name="warm")])
    return db.test_plan("Release", [smoke, tone])


def _policy(tmp_path, text=POLICY):
    path = tmp_path / "assay-gate.yml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_a_policy_tolerates_what_it_says_and_says_so(db, cli, release, tmp_path):
    junit = tmp_path / "assay.xml"

    code = cli("run", "test-plan", "Release", "--policy", _policy(tmp_path),
               "--junit", str(junit))

    assert code == EXIT_PASSED, cli.out.getvalue() + cli.err.getvalue()
    assert "  gate: Tone: 1 of 2 passed (50%) ≥ min 50%" in cli.out.getvalue()
    suites = [s.get("name") for s in ET.parse(junit).getroot().findall("testsuite")]
    assert sorted(suites[:-1]) == ["Smoke", "Tone"] and suites[-1] == "Assay gate"


def test_a_flag_tightens_one_test_set_for_one_run(db, cli, release, tmp_path):
    code = cli("run", "test-plan", "Release", "--policy", _policy(tmp_path),
               "--max-failures", "Tone=0", "--json")

    assert code == EXIT_FAILED
    outcome = json.loads(cli.out.getvalue())
    assert [r["text"] for r in outcome["gate"] if r["broken"]] == ["Tone: 1 failure > max 0"]


def test_limits_given_only_as_flags(db, cli, release):
    assert cli("run", "test-plan", "Release", "--max-failures", "1") == EXIT_PASSED
    assert cli("run", "test-plan", "Release", "--min-pass-rate", "0.9") == EXIT_FAILED
    assert "2 of 3 passed (66.7%) < min 90%" in cli.out.getvalue()


def test_a_blocking_check_fails_the_step_whatever_the_limits(db, cli, release):
    code = cli("run", "test-plan", "Release", "--max-failures", "5",
               "--blocking-check", "Misses")

    assert code == EXIT_FAILED
    assert "gate: blocking check 'Misses' failed in 1 run" in cli.out.getvalue()


def test_a_policy_naming_a_set_the_plan_doesnt_link_is_refused_before_it_starts(
        db, cli, release, tmp_path):
    path = _policy(tmp_path, POLICY.replace('"Tone"', '"Tonne"'))

    assert cli("run", "test-plan", "Release", "--policy", path) == EXIT_ERROR
    assert "names test set 'Tonne', which this test plan doesn't run" in cli.err.getvalue()
    assert db.dispatched == []  # nothing was started


def test_a_policy_for_another_scope_is_refused_before_it_starts(db, cli, release, tmp_path):
    db.test_plan("Nightly", [db.test_set("Smoke 2", [db.test(name="x")])])

    assert cli("run", "test-plan", "Nightly", "--policy", _policy(tmp_path)) == EXIT_ERROR
    assert "is for the test plan 'Release', not the test plan 'Nightly'" in cli.err.getvalue()
    assert db.dispatched == []


def test_a_policy_file_with_a_mistake_is_refused_before_any_request(tmp_path):
    class Untouched:
        def __getattr__(self, name):
            raise AssertionError(f"the API was called: {name}")

    err = io.StringIO()
    code = main(["run", "test-plan", "Release", "--policy",
                 _policy(tmp_path, "max_failure: 3\n")],
                client=Untouched(), env={}, out=io.StringIO(), err=err)

    assert code == EXIT_ERROR
    assert "unknown key 'max_failure'" in err.getvalue()


def test_per_set_limits_on_a_standalone_test_are_refused(db, cli):
    db.test(name="lonely")

    assert cli("run", "test", "lonely", "--max-failures", "Smoke=1") == EXIT_ERROR
    assert "which this test doesn't run (it runs: none)" in cli.err.getvalue()


def test_an_execution_that_failed_only_by_not_running_exits_4(db, cli):
    db.test_set("Live", [db.test(name="asks", model_output=None), db.test(name="recorded")])

    assert cli("run", "test-set", "Live") == EXIT_UNJUDGED
    assert "couldn't be judged: it failed only on runs that Not Ran" in cli.out.getvalue()
