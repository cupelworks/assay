# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Whether a finished run, execution or batch passes the gate, and what to say
about it. Pure functions over the API's replies: no I/O here."""
from dataclasses import dataclass, field

RUN_STATUSES = ("Green", "Amber", "Red", "NotRan")
"""A run's outcomes, once it has finished."""
RUN_FAIL_ON = ("Amber", "Red", "NotRan")
"""By default anything short of Green fails the gate."""

BATCH_FAIL_ON_CHOICES = ("fail", "inconclusive")
BATCH_FAIL_ON = ("fail",)
"""By default a check proven to fail fails the gate; `inconclusive` is opt-in."""
_BATCH_ALWAYS_FAIL = {"Incomplete": "it was stopped before every run ran",
                      "NotRan": "nothing could be decided: every run Not Ran, "
                                "or every check errored in every run"}
_BATCH_VERDICT_OF_STATUS = {"Passed": "pass", "Done": "pass",
                            "Failed": "fail", "Inconclusive": "inconclusive"}


@dataclass
class Case:
    """One line of the report, and one JUnit test case: a run, or a batch's check."""
    name: str
    group: str
    outcome: str  # passed | failed | error | skipped
    message: str = ""
    detail: str = ""


@dataclass
class Outcome:
    kind: str  # run | execution | batch
    id: str
    scope: dict
    status: str
    passed: bool
    summary: str
    counts: dict = field(default_factory=dict)
    cases: list[Case] = field(default_factory=list)
    fail_on: tuple = ()

    @property
    def failures(self) -> list[Case]:
        return [case for case in self.cases if case.outcome in ("failed", "error")]

    def to_json(self) -> dict:
        return {"kind": self.kind, "id": self.id, "scope": self.scope, "status": self.status,
                "passed": self.passed, "summary": self.summary, "counts": self.counts,
                "fail_on": list(self.fail_on),
                "failures": [{"name": c.name, "group": c.group, "outcome": c.outcome,
                              "message": c.message} for c in self.failures]}


def run_finished(counts: dict) -> bool:
    return not counts.get("Pending", 0) and not counts.get("Running", 0)


def batch_finished(batch: dict) -> bool:
    return batch.get("result") is not None or batch.get("status") in (
        *_BATCH_VERDICT_OF_STATUS, *_BATCH_ALWAYS_FAIL)


# -- runs and executions --

def _run_case(name: str, group: str, status: str, not_met: list, error: str | None,
              fail_on: tuple) -> Case:
    if status == "NotRan":
        message = f"Not Ran: {error or 'no reason recorded'}"
    elif status in ("Amber", "Red"):
        message = f"{status}: not met: {', '.join(not_met) or '—'}"
    else:
        message = status
    if status not in fail_on:
        return Case(name, group, "passed", "" if status == "Green" else message)
    return Case(name, group, "error" if status == "NotRan" else "failed", message)


def _gate_counts(counts: dict, fail_on: tuple) -> bool:
    return not any(counts.get(status, 0) for status in fail_on)


def _counts_line(counts: dict) -> str:
    return ", ".join(f"{counts.get(s, 0)} {s}" for s in RUN_STATUSES)


def execution_outcome(scope: dict, details: dict, runs: list[dict],
                      fail_on: tuple = RUN_FAIL_ON) -> Outcome:
    counts = {s: details["runs"].get(s, 0) for s in RUN_STATUSES}
    passed = _gate_counts(counts, fail_on)
    cases = [_run_case(run.get("test_name", run["id"]), scope["name"], run["status"],
                       (run.get("checks") or {}).get("not_met", []), run.get("error"), fail_on)
             for run in runs]
    noun = scope["kind"].replace("-", " ")
    summary = (f"{'PASSED' if passed else 'FAILED'}: {noun} {scope['name']!r}, "
               f"{details.get('run_count', sum(counts.values()))} runs: {_counts_line(counts)}")
    return Outcome("execution", str(details["id"]), scope, _execution_status(counts), passed,
                   summary, counts, cases, fail_on)


def _execution_status(counts: dict) -> str:
    """The execution's worst outcome, in the API's words."""
    for status in ("NotRan", "Red", "Amber", "Green"):
        if counts.get(status):
            return status
    return "Green"


def run_outcome(scope: dict, details: dict, fail_on: tuple = RUN_FAIL_ON) -> Outcome:
    status = details["status"]
    results = details.get("results") or {}
    not_met = sorted(label for label, r in results.items() if not r.get("passed"))
    case = _run_case(details.get("name", scope["name"]), scope["name"], status, not_met,
                     details.get("error"), fail_on)
    met = len(results) - len(not_met)
    passed = case.outcome == "passed"
    summary = (f"{'PASSED' if passed else 'FAILED'}: test {scope['name']!r}: {status}"
               + (f", {met}/{len(results)} checks met" if results else "")
               + (f" ({details['error']})" if details.get("error") else ""))
    return Outcome("run", str(details["id"]), scope, status, passed, summary,
                   {s: int(s == status) for s in RUN_STATUSES}, [case], fail_on)


# -- statistics batches --

def batch_outcome(batch: dict, fail_on: tuple = BATCH_FAIL_ON) -> Outcome:
    status = batch["status"]
    scope = {"kind": batch["scope"]["kind"].replace("_", "-"), "id": str(batch["scope"]["id"]),
             "name": batch["scope"]["name"]}
    if status in _BATCH_ALWAYS_FAIL:
        passed, why = False, _BATCH_ALWAYS_FAIL[status]
    else:
        verdict = _BATCH_VERDICT_OF_STATUS.get(status)
        passed, why = verdict not in fail_on, ""
    result = batch.get("result") or {}
    verdicts = result.get("verdicts") or batch.get("verdicts") or {}
    cases = []
    for entry in result.get("entries", []):
        group = " / ".join(x for x in (entry.get("test_set_name"), entry["name"]) if x)
        for check in entry.get("checks", []):
            cases.append(_check_case(group, check, fail_on))
    sentence = result.get("summary") or batch.get("summary") or why or status
    summary = (f"{'PASSED' if passed else 'FAILED'}: batch of {scope['kind'].replace('-', ' ')} "
               f"{scope['name']!r} ({batch['statistical_test']}): {status}. {sentence}")
    return Outcome("batch", str(batch["id"]), scope, status, passed, summary, verdicts, cases,
                   fail_on)


def _check_case(group: str, check: dict, fail_on: tuple) -> Case:
    label = check["label"]
    if not check.get("applies", True):
        return Case(label, group, "skipped", check.get("reason") or "no verdict for this check")
    statistic = check.get("statistic") or {}
    verdict = statistic.get("verdict")
    reason = statistic.get("reason") or check.get("reason") or ""
    message = f"{verdict or 'no verdict'}: {reason}".rstrip(": ")
    if verdict == "pass":
        return Case(label, group, "passed", message)
    if verdict in fail_on:
        return Case(label, group, "failed", message)
    return Case(label, group, "skipped", message)
