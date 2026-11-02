# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""`assay`: start a run or a statistics batch, wait for it, report it, and exit with
a code a pipeline step acts on: 0 passed, 1 failed the gate, 2 error, 3 timed out,
4 couldn't be judged, 130 interrupted, 143 cancelled."""
import argparse
import json
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

from assay_cli import arguments, report
from assay_cli.client import SCOPE_NOUNS, ApiError, AssayClient, TimeLeft
from assay_cli.gate import (
    IN_PROGRESS,
    RUN_STATUSES,
    STRICT,
    Outcome,
    Policy,
    PolicyError,
    batch_finished,
    batch_outcome,
    check_blocking_checks,
    check_test_sets,
    execution_outcome,
    run_finished,
    run_outcome,
    runs_disagree,
)
from assay_cli.policy import check_scope, load, with_flags
from assay_cli.waiting import TimedOut, Waiter

EXIT_PASSED = 0
EXIT_FAILED = 1
"""The gate failed: a run or a verdict the step fails on."""
EXIT_ERROR = 2
"""Nothing to judge: bad arguments, an unreachable API, a request refused, a reply
the CLI can't read whole (a status it doesn't know)."""
EXIT_TIMED_OUT = 3
"""It didn't finish within --timeout; nothing was concluded."""
EXIT_UNJUDGED = 4
"""It failed only because runs Not Ran: the application or the judge didn't answer,
so nothing about quality was decided."""
EXIT_INTERRUPTED = 130
"""Ctrl-C."""
EXIT_CANCELLED = 143
"""SIGTERM: the CI system cancelled the job."""

READ_SECONDS = 60
"""How long a read outside a wait is tried again: finding what to run, the health
check, a finished execution's runs."""
STOP_SECONDS = 5
"""How long stopping a batch may take: within the gap a CI system leaves between its
first signal and its SIGTERM (GitHub Actions: SIGINT, SIGTERM 7.5s later)."""


class Cancelled(Exception):
    """SIGTERM arrived, as a CI system sends when a job is cancelled."""


def _cancel(signum, frame):
    raise Cancelled("cancelled (SIGTERM)")


def _on_main_thread() -> bool:
    """Signals are only handled there."""
    return threading.current_thread() is threading.main_thread()


@contextmanager
def _sigterm_deferred() -> Iterator[None]:
    """A SIGTERM while the block runs waits for it to end, then ends the step as
    cancelled: the block is what a cancel needs done."""
    if not _on_main_thread():
        yield
        return
    arrived = []
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: arrived.append(signum))
    try:
        yield
    finally:
        _restore_sigterm(previous)
    if arrived:
        raise Cancelled("cancelled (SIGTERM)")


def _restore_sigterm(previous) -> None:
    # None: a handler not set from Python, which can't be put back
    signal.signal(signal.SIGTERM, signal.SIG_DFL if previous is None else previous)


class Runner:
    """One command: the client, the clock, and where to write."""

    def __init__(self, client: AssayClient, args: argparse.Namespace, env: Mapping[str, str],
                 out: TextIO, err: TextIO, sleep: Callable[[float], None],
                 clock: Callable[[], float]):
        self.client, self.args, self.env = client, args, env
        self.out, self.err, self.clock = out, err, clock
        self.waiter = Waiter(clock, sleep, getattr(args, "interval", arguments.INTERVAL),
                             self.say)

    def say(self, message: str) -> None:
        print(message, file=self.err, flush=True)

    # -- commands --

    def health(self) -> int:
        health = self._fetch(lambda left: self.client.health(left), "Couldn't check Assay")
        status = health.get("status")
        workers = len(health.get("workers") or [])
        print(f"Assay {health.get('version', '?')}: {status} ({workers} worker"
              f"{'' if workers == 1 else 's'})", file=self.out)
        return EXIT_PASSED if status == "ready" else EXIT_FAILED

    def run(self) -> int:
        args = self.args
        policy = self._policy()  # read before any request: a mistake in it costs nothing
        self._check_reports()
        target = self._resolve()
        check_scope(policy, args.scope, target)
        self._check_policy(policy, target)
        scope = {"kind": args.scope, **target}
        kind = "run" if args.scope == "test" else "execution"
        started = self._start(
            lambda: self.client.start_run(args.scope, target["id"], args.replay), kind)
        run_id = str(started["id"])
        if args.scope == "test":
            self.say(f"Started run {run_id} of test {target['name']!r}.")
        else:
            self.say(f"Started execution {run_id} of {SCOPE_NOUNS[args.scope]} "
                     f"{target['name']!r}: {started.get('run_count', '?')} runs.")
        if args.no_wait:
            return self._started(kind, run_id, scope)

        if args.scope == "test":
            details = self.waiter.until(
                lambda left: self.client.standalone_run(target["id"], run_id, left),
                lambda d: d["status"] not in IN_PROGRESS, lambda d: d["status"], args.timeout)
            return self._report(run_outcome(scope, details, policy))
        details = self.waiter.until(
            lambda left: self.client.execution(args.scope, target["id"], run_id, left),
            lambda d: run_finished(d["runs"]),
            lambda d: _progress(d["runs"], d.get("run_count")), args.timeout)
        runs = self._finished_runs(target, details)
        return self._report(execution_outcome(scope, details, runs, policy))

    def batch(self) -> int:
        args = self.args
        self._check_reports()
        target = self._resolve()
        body = {f"{args.scope.replace('-', '_')}_id": target["id"],
                "statistical_test": args.statistical_test,
                "parameters": dict(args.param)}
        if args.times:
            body["times"] = args.times
        if args.note:
            body["note"] = args.note
        batch_id = None
        try:
            started = self._start(lambda: self.client.start_batch(body), "batch")
            batch_id = str(started["id"])
            progress = started.get("progress") or {}
            self.say(f"Started batch {batch_id} of {SCOPE_NOUNS[args.scope]} "
                     f"{target['name']!r}: {args.statistical_test}, "
                     f"{progress.get('times_requested', '?')} times, "
                     f"{progress.get('runs_total', '?')} runs.")
            if args.no_wait:
                return self._started("batch", batch_id, {"kind": args.scope, **target})
            batch = self.waiter.until(
                lambda left: self.client.batch(batch_id, time_left=left), batch_finished,
                _batch_progress, args.timeout)
        except (Exception, KeyboardInterrupt) as exc:
            # however it ends without a result once started: timeout, cancel, any error
            if args.stop_on_timeout and batch_id:
                self._stop(batch_id)
            elif args.stop_on_timeout and _may_have_started(exc):
                # the start's reply, with the batch's id, never came: nothing to stop by
                self.say("--stop-on-timeout can't stop it: the batch may have started, but "
                         "its id never came back. Stop it in Assay, from its batches list.")
            raise
        return self._report(batch_outcome(batch, args.fail_on))

    # -- the steps they share --

    def _finished_runs(self, target: dict, details: dict) -> list[dict]:
        """An execution's runs, once its counts (`details`) say every one has
        finished, read until the list agrees with those counts, for READ_SECONDS:
        a list read a moment behind them (a run still in progress, one missing)
        is never judged. Still in progress then is a timeout; still differing
        otherwise, an error: neither says anything about quality."""
        args = self.args
        deadline = self.clock() + READ_SECONDS  # one minute for every read, retries included
        while True:
            runs = self.waiter.read(
                lambda left: self.client.execution_runs(args.scope, target["id"],
                                                        str(details["id"]), left),
                deadline,
                lambda exc: ApiError(f"It finished, but its runs couldn't be read: {exc}",
                                     exc.status_code))
            unfinished = sum(run["status"] in IN_PROGRESS for run in runs)
            differs = runs_disagree(details, runs)
            if not unfinished and not differs:
                return runs
            if self.waiter.past(deadline):
                if unfinished:
                    raise TimedOut(f"It finished, but its runs still listed {unfinished} in "
                                   f"progress after {READ_SECONDS}s")
                raise ApiError(f"It finished, but after {READ_SECONDS}s {differs}")
            self.waiter.pause(deadline)

    def _fetch(self, read: Callable, failed: str) -> Any:
        """A read outside a wait, tried again for READ_SECONDS: then `failed`, an error."""
        return self.waiter.read(read, self.clock() + READ_SECONDS,
                                lambda exc: ApiError(f"{failed}: {exc}", exc.status_code))

    def _policy(self) -> Policy:
        args = self.args
        return with_flags(load(args.policy) if args.policy else STRICT, args.fail_on,
                          args.max_failures, args.min_pass_rate, args.blocking_check)

    def _check_policy(self, policy: Policy, target: dict) -> None:
        """Before it starts: a limit on a test set it doesn't run would never apply,
        and a blocking check it doesn't ask would always hold."""
        if not (policy.test_sets or policy.blocking_checks):
            return
        noun = SCOPE_NOUNS[self.args.scope]
        test_sets, labels = self._what_runs(target, bool(policy.blocking_checks))
        check_test_sets(policy, test_sets, noun)
        if policy.blocking_checks:
            check_blocking_checks(policy, labels, noun)

    def _what_runs(self, target: dict, with_labels: bool) -> tuple[list[str], list[str]]:
        """The test sets a run of `target` runs, and its checks' labels when
        `with_labels`. A replay runs exactly the entries of the execution it
        replays, whatever the plan links now. Each read is tried again on its own."""
        args, client = self.args, self.client
        noun = SCOPE_NOUNS[args.scope]

        def read(request: Callable[[TimeLeft], Any]) -> Any:
            return self._fetch(request, f"Couldn't read what the {noun} runs")

        if args.scope == "test":
            if not with_labels:
                return [], []
            return [], read(lambda left: client.test_labels(target["id"], left))
        entry_ids = None  # a live run runs every entry
        # a replay's own runs say which entries it runs, and, for a plan, their sets
        if args.replay and (with_labels or args.scope == "test-plan"):
            runs = read(lambda left: client.execution_runs(args.scope, target["id"],
                                                           args.replay, left))
            entry_ids = {str(run["test_set_entry_id"]["id"]) for run in runs}
        if args.scope == "test-set":
            sets = {target["id"]: target["name"]}
        elif args.replay:
            sets = {str(run["test_set_id"]["id"]): run["test_set_name"]
                    for run in runs if run.get("test_set_id")}
        else:
            sets = {s["id"]: s["name"]
                    for s in read(lambda left: client.plan_test_sets(target["id"], left))}
        if not with_labels:
            return list(sets.values()), []
        by_entry: dict[str, list[str]] = {}
        for set_id in sets:
            by_entry.update(read(lambda left, set_id=set_id: client.entry_labels(set_id, left)))
        if entry_ids is None:
            return list(sets.values()), [label for labels in by_entry.values()
                                         for label in labels]
        # an entry unlinked from its set since is replayed all the same: its checks
        # are read from its run, which keeps them; one read for each such entry
        for run in runs:
            entry_id = str(run["test_set_entry_id"]["id"])
            if entry_id not in by_entry:
                by_entry[entry_id] = read(
                    lambda left, run_id=str(run["id"]): client.run_labels(
                        args.scope, target["id"], args.replay, run_id, left))
        return list(sets.values()), [label for entry_id in entry_ids
                                     for label in by_entry[entry_id]]

    def _resolve(self) -> dict:
        args = self.args
        return self._fetch(lambda left: self.client.resolve(args.scope, args.ref, left),
                           f"Couldn't look up the {SCOPE_NOUNS[args.scope]} {args.ref!r}")

    def _start(self, start: Callable[[], dict], what: str) -> dict:
        """Never tried again, as Assay may have carried it out; an interrupt while
        it's in flight says so too."""
        try:
            return start()
        except (KeyboardInterrupt, Cancelled):
            self.say(f"Interrupted while starting the {what}: it may have started, so "
                     "look in Assay before running it again.")
            raise

    def _stop(self, batch_id: str) -> None:
        """Best effort, and quick: a failed or interrupted stop is reported, and the
        step's own ending kept, but for a SIGTERM meanwhile, which ends it as
        cancelled once the stop is done."""
        with _sigterm_deferred():
            try:
                self.client.stop_batch(batch_id,
                                       self.waiter.time_left(self.clock() + STOP_SECONDS))
            except (ApiError, KeyboardInterrupt) as exc:
                reason = exc if isinstance(exc, ApiError) else "interrupted"
                self.say(f"Couldn't stop batch {batch_id} ({reason}): its remaining runs "
                         "may still run; stop it in Assay.")
            else:
                self.say(f"Stopped batch {batch_id}: its remaining runs are cancelled.")

    def _started(self, kind: str, started_id: str, scope: dict) -> int:
        if self.args.json:
            print(json.dumps({"kind": kind, "id": started_id, "scope": scope}), file=self.out)
        else:
            print(started_id, file=self.out)
        return EXIT_PASSED

    def _summaries(self) -> list[Path]:
        """The files the Markdown summary is appended to, each once."""
        given = (self.args.markdown, self.env.get("GITHUB_STEP_SUMMARY"))
        return list(dict.fromkeys(Path(p).resolve() for p in given if p))

    def _check_reports(self) -> None:
        """Before it starts: a JUnit report written where a Markdown summary is then
        appended would no longer be XML."""
        junit = self.args.junit
        if junit and junit.resolve() in self._summaries():
            raise arguments.ArgumentError(
                f"--junit {junit}: the Markdown summary is appended to that file too "
                "(--markdown, or $GITHUB_STEP_SUMMARY): give the JUnit report a file of "
                "its own")

    def _report(self, outcome: Outcome) -> int:
        # the files first: they're what the CI system keeps; one that can't be
        # written leaves the others and the outcome, and makes the step an error
        unwritten = {}
        if self.args.junit:
            junit = report.junit(outcome)
            unwritten[self.args.junit] = _write(self.args.junit,
                                                lambda path: path.write_bytes(junit))
        summary = report.markdown(outcome)
        for target in self._summaries():
            unwritten[target] = _write(target, lambda path: _append(path, summary))
        unwritten = {path: reason for path, reason in unwritten.items() if reason}
        if self.args.json:
            print(json.dumps(outcome.to_json(), indent=2), file=self.out)
        else:
            print(report.text(outcome), file=self.out)
        if self.args.junit and self.args.junit not in unwritten:
            self.say(f"JUnit report: {self.args.junit}")
        for path, reason in unwritten.items():
            self.say(f"assay: couldn't write {path}: {reason}")
        if unwritten:
            return EXIT_ERROR
        if outcome.passed:
            return EXIT_PASSED
        if outcome.unreadable:
            return EXIT_ERROR  # nothing about quality was decided
        return EXIT_FAILED if outcome.judged else EXIT_UNJUDGED


def _may_have_started(exc: BaseException) -> bool:
    """A start that ended this way may have been carried out: no reply came."""
    if isinstance(exc, ApiError):
        return exc.maybe_done
    return isinstance(exc, KeyboardInterrupt | Cancelled)


def _write(path: Path, write: Callable[[Path], object]) -> str:
    """"" once written, else why it couldn't be."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write(path)
    except OSError as exc:
        return exc.strerror or str(exc)
    return ""


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(text)


def _progress(counts: dict, total: int | None) -> str:
    done = sum(counts.get(s, 0) for s in RUN_STATUSES)
    if total is None:
        total = done + sum(counts.get(s, 0) for s in IN_PROGRESS)
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


def _utf8(stream: TextIO) -> TextIO:
    """The report's marks and any test's name, whatever the locale: a pipe on a
    Windows runner is cp1252 otherwise, and can't hold them."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure and (stream.encoding or "").lower().replace("-", "") != "utf8":
        reconfigure(encoding="utf-8", errors="replace")
    return stream


def main(argv: list[str] | None = None, *, client: AssayClient | None = None,
         env: Mapping[str, str] | None = None, out: TextIO | None = None,
         err: TextIO | None = None, sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic) -> int:
    env = os.environ if env is None else env
    out = out or _utf8(sys.stdout)
    err = err or _utf8(sys.stderr)
    args = arguments.parse(argv)
    # a CI system cancels a job with SIGTERM: end the wait like Ctrl-C does, so a
    # batch can still be stopped
    on_main_thread = _on_main_thread()
    previous = signal.signal(signal.SIGTERM, _cancel) if on_main_thread else None
    try:
        return _run(args, client, env, out, err, sleep, clock)
    except Cancelled as exc:
        # one landing as the step was already ending: in a handler, or cleaning up
        print(f"assay: {exc}", file=err)
        return EXIT_CANCELLED
    finally:
        if on_main_thread:
            _restore_sigterm(previous)


def _run(args: argparse.Namespace, client: AssayClient | None, env: Mapping[str, str],
         out: TextIO, err: TextIO, sleep: Callable[[float], None],
         clock: Callable[[], float]) -> int:
    owned = client is None
    try:
        if owned:
            url = (args.url or env.get(arguments.URL_ENV) or "").strip()
            if not url:
                print(f"assay: give the API's URL with --url or ${arguments.URL_ENV}", file=err)
                return EXIT_ERROR
            client = AssayClient.connect(arguments.api_url(url), arguments.headers(args, env),
                                         args.http_timeout)
        runner = Runner(client, args, env, out, err, sleep, clock)
        return getattr(runner, args.command)()
    except (ApiError, arguments.ArgumentError, PolicyError) as exc:
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
        if owned and client is not None:
            client.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
