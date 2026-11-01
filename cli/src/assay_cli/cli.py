# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""`assay`: start a run or a statistics batch, wait for it, report it, and exit
0 or 1 so a pipeline step passes or fails with it."""
import argparse
import json
import math
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from assay_cli import __version__, report
from assay_cli.client import SCOPE_NOUNS, SCOPES, ApiError, AssayClient
from assay_cli.gate import (
    BATCH_FAIL_ON,
    BATCH_FAIL_ON_CHOICES,
    RUN_FAIL_ON,
    RUN_STATUSES,
    Outcome,
    batch_finished,
    batch_outcome,
    execution_outcome,
    run_finished,
    run_outcome,
)

EXIT_PASSED = 0
EXIT_FAILED = 1
"""The gate failed: a run or a verdict the step fails on."""
EXIT_ERROR = 2
"""Nothing to judge: bad arguments, an unreachable API, a request refused."""
EXIT_TIMED_OUT = 3
"""It didn't finish within --timeout; nothing was concluded."""
EXIT_INTERRUPTED = 130
"""Ctrl-C."""
EXIT_CANCELLED = 143
"""SIGTERM: the CI system cancelled the job."""

RUNS_READ_SECONDS = 60
"""After an execution finishes, how long reading its runs is tried again."""
LAST_CHECK_SECONDS = 5
"""A check made at the deadline still gets this long to be answered."""

URL_ENV = "ASSAY_API_URL"
HEADERS_ENV = "ASSAY_API_HEADERS"


class TimedOut(Exception):
    pass


class Cancelled(Exception):
    """SIGTERM arrived, as a CI system sends when a job is cancelled."""


def _cancel(signum, frame):
    raise Cancelled("cancelled (SIGTERM)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assay",
        description="Run Assay's tests from a pipeline and fail the step when they fail.",
        epilog="Exit codes: 0 passed, 1 failed the gate, 2 error, 3 timed out, "
               "130 interrupted, 143 cancelled.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _connection_arguments(parser, defaults=True)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    # the connection's options are accepted after the command too
    connection = argparse.ArgumentParser(add_help=False)
    _connection_arguments(connection, defaults=False)

    commands.add_parser("health", parents=[connection],
                        help="Check the API is reachable and a worker is ready.")

    run = commands.add_parser(
        "run", parents=[connection],
        help="Run a test, test set or plan once and gate on its runs' outcomes.",
        description="Start a run (a live execution, or a replay of a past one), wait for "
                    "every run to finish, and fail when any run's outcome is in --fail-on.")
    _scope_arguments(run)
    run.add_argument("--replay", metavar="EXECUTION_ID",
                     help="Replay this past execution of the set or plan: exactly its entries.")
    run.add_argument("--fail-on", type=_choices(RUN_STATUSES), default=RUN_FAIL_ON,
                     metavar="STATUSES",
                     help="Comma-separated run outcomes that fail the step "
                          f"(default {','.join(RUN_FAIL_ON)}).")
    _wait_arguments(run)
    _output_arguments(run)

    batch = commands.add_parser(
        "batch", parents=[connection],
        help="Run a scope N times as a statistics batch and gate on its verdicts.",
        description="Start a statistics batch, wait for its result, and fail when a check's "
                    "verdict is in --fail-on, or when the batch couldn't decide anything.")
    _scope_arguments(batch)
    batch.add_argument("--statistical-test", "-s", required=True, metavar="ID",
                       help="A batch test's id from GET /statistics/tests.")
    batch.add_argument("--times", "-n", type=_positive_int, metavar="N",
                       help="Times to run the scope (default: the catalogue's suggestion).")
    batch.add_argument("--param", "-p", action="append", default=[], type=_parameter,
                       metavar="KEY=VALUE",
                       help="A parameter of the statistical test, e.g. target=0.9; "
                            "repeatable. VALUE is read as JSON when it parses.")
    batch.add_argument("--note", help="Kept with the batch, e.g. the commit or PR it tests.")
    batch.add_argument("--fail-on", type=_choices(BATCH_FAIL_ON_CHOICES), default=BATCH_FAIL_ON,
                       metavar="VERDICTS",
                       help="Comma-separated verdicts that fail the step: fail, inconclusive "
                            f"(default {','.join(BATCH_FAIL_ON)}). A check proven to fail, and "
                            "a batch that was stopped or couldn't decide anything, always "
                            "fail it.")
    batch.add_argument("--stop-on-timeout", action="store_true",
                       help="Stop the batch when the wait ends without its result "
                            "(--timeout reached, the job cancelled, Ctrl-C), so its "
                            "remaining runs aren't paid for.")
    _wait_arguments(batch)
    _output_arguments(batch)
    return parser


def _connection_arguments(parser, defaults: bool) -> None:
    """On the subcommands, an option left out mustn't overwrite one given before them."""
    def default(value):
        return value if defaults else argparse.SUPPRESS
    parser.add_argument("--url", default=default(None),
                        help=f"Assay's API, e.g. https://assay.internal/api (or ${URL_ENV}).")
    parser.add_argument("--header", action="append", default=[] if defaults else None,
                        dest="header" if defaults else "header_after", metavar="'NAME: VALUE'",
                        help="A header sent with every request, e.g. for a gateway in front "
                             f"of the API; repeatable. ${HEADERS_ENV} takes a JSON object.")
    parser.add_argument("--http-timeout", type=_positive_float, default=default(30.0),
                        metavar="SECONDS",
                        help="Timeout of each request to the API (default 30).")


def _scope_arguments(parser) -> None:
    parser.add_argument("scope", choices=SCOPES, help="What to run.")
    parser.add_argument("ref", metavar="ID_OR_NAME", help="Its id, or its exact name.")


def _wait_arguments(parser) -> None:
    parser.add_argument("--no-wait", action="store_true",
                        help="Start it, print its id and exit 0 without waiting.")
    parser.add_argument("--timeout", type=_non_negative_float, default=3600, metavar="SECONDS",
                        help="Give up waiting after this long (default 3600; 0 waits forever).")
    parser.add_argument("--interval", type=_positive_float, default=5, metavar="SECONDS",
                        help="Time between checks on its progress (default 5).")


def _output_arguments(parser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="Print the outcome as JSON instead of text.")
    parser.add_argument("--junit", type=Path, metavar="FILE",
                        help="Write a JUnit XML report, for the CI system's test results.")
    parser.add_argument("--markdown", type=Path, metavar="FILE",
                        help="Append a Markdown summary to FILE. On GitHub Actions it's "
                             "appended to $GITHUB_STEP_SUMMARY without this.")


def _choices(allowed: tuple) -> Callable[[str], tuple]:
    def parse(value: str) -> tuple:
        lowered = {a.lower(): a for a in allowed}
        chosen = [v.strip() for v in value.split(",") if v.strip()]
        unknown = [v for v in chosen if v.lower() not in lowered]
        if unknown or not chosen:
            raise argparse.ArgumentTypeError(
                f"choose from {', '.join(allowed)} (got {value!r})")
        return tuple(dict.fromkeys(lowered[v.lower()] for v in chosen))
    return parse


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if not (number > 0 and math.isfinite(number)):  # NaN too
        raise argparse.ArgumentTypeError("must be a number more than 0")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if not (number >= 0 and math.isfinite(number)):
        raise argparse.ArgumentTypeError("must be a number, 0 or more")
    return number


def _parameter(pair: str) -> tuple[str, object]:
    """VALUE as JSON when it parses (`0.9`, `true`), else as text."""
    key, sep, value = pair.partition("=")
    if not sep or not key.strip():
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE (got {pair!r})")
    try:
        return key.strip(), json.loads(value)
    except ValueError:
        return key.strip(), value


def _headers(args, env) -> dict[str, str]:
    """$ASSAY_API_HEADERS, then each --header: a later one replaces an earlier one
    of the same name, whatever its case, as HTTP names are."""
    headers = {}

    def put(name: str, value: str) -> None:
        for given in [given for given in headers if given.lower() == name.lower()]:
            del headers[given]
        headers[name] = value

    if env.get(HEADERS_ENV):
        try:
            loaded = json.loads(env[HEADERS_ENV])
        except ValueError as exc:
            raise ApiError(f"${HEADERS_ENV} isn't a JSON object: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ApiError(f"${HEADERS_ENV} must be a JSON object of header names to values")
        for name, value in loaded.items():
            put(str(name), str(value))
    for header in [*args.header, *(getattr(args, "header_after", None) or [])]:
        name, sep, value = header.partition(":")
        if not sep or not name.strip():
            raise ApiError(f"--header {header!r}: expected 'NAME: VALUE'")
        put(name.strip(), value.strip())
    return headers


class Runner:
    """One invocation: the client, the clock, and where to write."""

    def __init__(self, client: AssayClient, args, env, out, err,
                 sleep: Callable[[float], None], clock: Callable[[], float]):
        self.client, self.args, self.env = client, args, env
        self.out, self.err, self.sleep, self.clock = out, err, sleep, clock

    def say(self, message: str) -> None:
        print(message, file=self.err, flush=True)

    # -- commands --

    def health(self) -> int:
        health = self.client.health()
        status = health.get("status")
        workers = len(health.get("workers") or [])
        print(f"Assay {health.get('version', '?')}: {status} ({workers} worker"
              f"{'' if workers == 1 else 's'})", file=self.out)
        return EXIT_PASSED if status == "ready" else EXIT_FAILED

    def run(self) -> int:
        args = self.args
        target = self.client.resolve(args.scope, args.ref)
        scope = {"kind": args.scope, **target}
        started = self.client.start_run(args.scope, target["id"], args.replay)
        run_id = str(started["id"])
        if args.scope == "test":
            self.say(f"Started run {run_id} of test {target['name']!r}.")
        else:
            self.say(f"Started execution {run_id} of {SCOPE_NOUNS[args.scope]} "
                     f"{target['name']!r}: {started.get('run_count', '?')} runs.")
        if args.no_wait:
            return self._started("run" if args.scope == "test" else "execution", run_id, scope)

        if args.scope == "test":
            details = self._wait(lambda: self.client.standalone_run(target["id"], run_id),
                                 lambda d: d["status"] not in ("Pending", "Running"),
                                 lambda d: d["status"])
            outcome = run_outcome(scope, details, args.fail_on)
        else:
            details = self._wait(
                lambda: self.client.execution(args.scope, target["id"], run_id),
                lambda d: run_finished(d["runs"]),
                lambda d: _progress(d["runs"], d.get("run_count")))
            # finished: its own time to read the runs, and a failure is an error
            runs = self._read(
                lambda: self.client.execution_runs(args.scope, target["id"], run_id),
                self.clock() + RUNS_READ_SECONDS,
                lambda exc: ApiError(f"It finished, but its runs couldn't be read: {exc}",
                                     exc.status_code))
            outcome = execution_outcome(scope, details, runs, args.fail_on)
        return self._report(outcome)

    def batch(self) -> int:
        args = self.args
        target = self.client.resolve(args.scope, args.ref)
        body = {f"{args.scope.replace('-', '_')}_id": target["id"],
                "statistical_test": args.statistical_test,
                "parameters": dict(args.param)}
        if args.times:
            body["times"] = args.times
        if args.note:
            body["note"] = args.note
        batch_id = None
        try:
            started = self.client.start_batch(body)
            batch_id = str(started["id"])
            progress = started.get("progress") or {}
            self.say(f"Started batch {batch_id} of {SCOPE_NOUNS[args.scope]} "
                     f"{target['name']!r}: {args.statistical_test}, "
                     f"{progress.get('times_requested', '?')} times, "
                     f"{progress.get('runs_total', '?')} runs.")
            if args.no_wait:
                return self._started("batch", batch_id, {"kind": args.scope, **target})
            batch = self._wait(lambda: self.client.batch(batch_id), batch_finished,
                               _batch_progress)
        except (Exception, KeyboardInterrupt):
            # however it ends without a result once started: timeout, cancel, any error
            if args.stop_on_timeout and batch_id:
                self._stop(batch_id)
            raise
        return self._report(batch_outcome(batch, args.fail_on))

    def _stop(self, batch_id: str) -> None:
        """Best effort: a failed stop is reported, and the wait's own ending kept."""
        try:
            self.client.stop_batch(batch_id)
        except ApiError as exc:
            self.say(f"Couldn't stop batch {batch_id} ({exc}): its remaining runs may "
                     "still run; stop it in Assay.")
        else:
            self.say(f"Stopped batch {batch_id}: its remaining runs are cancelled.")

    # -- waiting and reporting --

    def _wait(self, read, finished, describe):
        deadline = self.clock() + self.args.timeout if self.args.timeout > 0 else None
        timeout = f"Still not finished after {self.args.timeout:g}s"
        last = None
        while True:
            current = self._read(read, deadline,
                                 lambda exc: TimedOut(f"{timeout}: the last check failed ({exc})"))
            line = describe(current)
            if line != last:
                self.say(f"  {line}")
                last = line
            if finished(current):
                return current
            if self._past(deadline):
                raise TimedOut(f"{timeout}: {line}")
            self._pause(deadline)

    def _read(self, read, deadline: float | None,
              out_of_time: Callable[[ApiError], Exception]):
        """A read that changes nothing, so a failure that may pass is tried again
        until the deadline; any other refusal ends it. Each try waits no longer than
        the time left (a check at the deadline gets LAST_CHECK_SECONDS)."""
        while True:
            left = None if deadline is None else max(deadline - self.clock(),
                                                      LAST_CHECK_SECONDS)
            try:
                with self.client.within(left):
                    return read()
            except ApiError as exc:
                if not exc.transient:
                    raise
                if self._past(deadline):
                    raise out_of_time(exc) from exc
                self.say(f"  {exc}; trying again")
                self._pause(deadline)

    def _past(self, deadline: float | None) -> bool:
        return deadline is not None and self.clock() >= deadline

    def _pause(self, deadline: float | None) -> None:
        """The interval, but never past the deadline: the last check comes on time."""
        wait = self.args.interval
        if deadline is not None:
            wait = min(wait, max(deadline - self.clock(), 0.0))
        self.sleep(wait)

    def _started(self, kind: str, started_id: str, scope: dict) -> int:
        if self.args.json:
            print(json.dumps({"kind": kind, "id": started_id, "scope": scope}), file=self.out)
        else:
            print(started_id, file=self.out)
        return EXIT_PASSED

    def _report(self, outcome: Outcome) -> int:
        # the files first: they're what the CI system keeps
        if self.args.junit:
            self.args.junit.parent.mkdir(parents=True, exist_ok=True)
            self.args.junit.write_bytes(report.junit(outcome))
        given = (self.args.markdown, self.env.get("GITHUB_STEP_SUMMARY"))
        for path in dict.fromkeys(Path(p).resolve() for p in given if p):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as summary:
                summary.write(report.markdown(outcome))
        if self.args.json:
            print(json.dumps(outcome.to_json(), indent=2), file=self.out)
        else:
            print(report.text(outcome), file=self.out)
        if self.args.junit:
            self.say(f"JUnit report: {self.args.junit}")
        return EXIT_PASSED if outcome.passed else EXIT_FAILED


def _progress(counts: dict, total: int | None) -> str:
    done = sum(counts.get(s, 0) for s in RUN_STATUSES)
    total = total if total is not None else done + counts.get("Pending", 0) + counts.get(
        "Running", 0)
    shown = ", ".join(f"{counts[s]} {s}" for s in RUN_STATUSES if counts.get(s))
    return f"{done}/{total} runs finished" + (f" ({shown})" if shown else "")


def _batch_progress(batch: dict) -> str:
    progress = batch.get("progress") or {}
    line = (f"{batch.get('status')}: {progress.get('runs_done', 0)}/"
            f"{progress.get('runs_total', '?')} runs finished, "
            f"{progress.get('times_done', 0)}/{progress.get('times_requested', '?')} times")
    waves = progress.get("waves")
    if waves:
        line += f", wave {waves.get('released')}/{waves.get('planned')}"
    return line


def _utf8(stream):
    """The report's marks and any test's name, whatever the locale: a pipe on a
    Windows runner is cp1252 otherwise, and can't hold them."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure and (stream.encoding or "").lower().replace("-", "") != "utf8":
        reconfigure(encoding="utf-8", errors="replace")
    return stream


def main(argv: list[str] | None = None, *, client: AssayClient | None = None,
         env=None, out=None, err=None, sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic) -> int:
    env = os.environ if env is None else env
    out = out or _utf8(sys.stdout)
    err = err or _utf8(sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "replay", None) and args.scope == "test":
        parser.error("--replay: a standalone test has no executions to replay")
    owned = client is None
    # a CI system cancels a job with SIGTERM: end the wait like Ctrl-C does, so a
    # batch can still be stopped (signals are only handled on the main thread)
    on_main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, _cancel) if on_main_thread else None
    try:
        if owned:
            url = args.url or env.get(URL_ENV)
            if not url:
                print(f"assay: give the API's URL with --url or ${URL_ENV}", file=err)
                return EXIT_ERROR
            client = AssayClient.connect(url, _headers(args, env), args.http_timeout)
        runner = Runner(client, args, env, out, err, sleep, clock)
        return getattr(runner, args.command)()
    except ApiError as exc:
        print(f"assay: {exc}", file=err)
        return EXIT_ERROR
    except TimedOut as exc:
        print(f"assay: {exc}", file=err)
        return EXIT_TIMED_OUT
    except Cancelled as exc:
        print(f"assay: {exc}", file=err)
        return EXIT_CANCELLED
    except KeyboardInterrupt:
        print("assay: interrupted", file=err)
        return EXIT_INTERRUPTED
    except Exception as exc:
        # anything unforeseen is an error, never a failed gate (exit 1)
        print(f"assay: unexpected error: {type(exc).__name__}: {exc}", file=err)
        return EXIT_ERROR
    finally:
        if on_main_thread:
            # None: a handler not set from Python, which can't be put back
            signal.signal(signal.SIGTERM, signal.SIG_DFL if previous is None else previous)
        if owned and client is not None:
            client.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
