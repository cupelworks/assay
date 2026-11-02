# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Whether a finished run, execution or batch passes the gate, by which rules, and
what to say about it. Pure functions over the API's replies: no I/O here. A status
this CLI doesn't know never passes: the gate fails closed.

A run or an execution is judged by a policy: which outcomes fail a run, how many
failing runs the execution may have (over the whole of it, or per test set), and
which checks may never fail. With none given, any failing run fails the gate."""
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from assay_cli.client import SCOPE_NOUNS

IN_PROGRESS = ("Pending", "Running")
"""A run's or a batch's statuses before it has finished."""
RUN_STATUSES = ("Green", "Amber", "Red", "NotRan")
"""A run's outcomes, once it has finished."""
RUN_FAIL_ON = ("Amber", "Red", "NotRan")
"""By default anything short of Green fails the gate."""

BATCH_FAIL_ON_CHOICES = ("fail", "inconclusive")
BATCH_FAIL_ON = ("fail",)
"""A check proven to fail always fails the gate; `inconclusive` is opt-in, and adds to it."""
_BATCH_ALWAYS_FAIL = {"Incomplete": "it was stopped before every run ran",
                      "NotRan": "nothing could be decided: every run Not Ran, "
                                "or every check errored in every run"}
_BATCH_VERDICT_OF_STATUS = {"Passed": "pass", "Done": "pass",
                            "Failed": "fail", "Inconclusive": "inconclusive"}
"""The verdict a status stands for, when a batch has no verdicts counted."""


def spelled(allowed: tuple[str, ...], values: Iterable[str]) -> tuple[str, ...]:
    """`values` in any case, each once, spelled as `allowed` spells them; ValueError
    naming the choices when one isn't among them, or none is given."""
    lowered = {a.lower(): a for a in allowed}
    chosen = [str(v).strip() for v in values if str(v).strip()]
    if not chosen or any(v.lower() not in lowered for v in chosen):
        raise ValueError(f"choose from {', '.join(allowed)} (got {', '.join(chosen) or 'none'})")
    return tuple(dict.fromkeys(lowered[v.lower()] for v in chosen))


class PolicyError(Exception):
    """A policy that can't be applied: a file or a flag that's wrong, or one naming
    a test set the execution didn't run."""


def _unknown(statuses: str) -> str:
    return f"Assay reported a status this CLI doesn't know ({statuses}): update the CLI"


# -- the policy --

@dataclass(frozen=True)
class Limits:
    """How many failing runs a group of runs may have: at most `max_failures`, and a
    share of passing runs of at least `min_pass_rate` (0 to 1). None: no such limit."""
    max_failures: int | None = None
    min_pass_rate: float | None = None

    @property
    def given(self) -> bool:
        return self.max_failures is not None or self.min_pass_rate is not None

    def updated(self, other: "Limits") -> "Limits":
        """These limits, with each one `other` gives instead."""
        return Limits(self.max_failures if other.max_failures is None else other.max_failures,
                      self.min_pass_rate if other.min_pass_rate is None else other.min_pass_rate)


@dataclass(frozen=True)
class Policy:
    """What a run or an execution is judged by."""
    fail_on: tuple[str, ...] = RUN_FAIL_ON
    limits: Limits = Limits()
    """Over every run of the execution."""
    test_sets: Mapping[str, Limits] = field(default_factory=lambda: MappingProxyType({}))
    """Over each named test set's runs, by the set's name."""
    blocking_checks: tuple[str, ...] = ()
    """Check labels that fail the gate whenever one fails, whatever the counts:
    matched ignoring letter case, as a test's labels are unique."""
    scope: tuple[str, str] | None = None
    """The scope a file is for, as (kind, name or id), when it names one."""
    source: str | None = None
    """The file it was read from."""

    @property
    def tolerant(self) -> bool:
        """It says more than "any failing run fails the gate"."""
        return self.limits.given or bool(self.test_sets) or bool(self.blocking_checks)


STRICT = Policy()
"""The default: any run short of Green fails the gate."""


@dataclass(frozen=True)
class Rule:
    """One rule of a policy, as it held on these runs."""
    rule: str  # max_failures | min_pass_rate | none_allowed | blocking_check
    test_set: str | None
    limit: object
    actual: object
    broken: bool
    name: str
    """The rule alone, the same from one build to the next: a JUnit case's name."""
    text: str
    """The rule and how it held, in a few words."""

    @property
    def tolerated(self) -> bool:
        """It held although runs failed."""
        if self.broken or self.rule == "blocking_check":
            return False
        return self.actual < 1 if self.rule == "min_pass_rate" else self.actual > 0

    def to_json(self) -> dict:
        return {"rule": self.rule, "test_set": self.test_set, "limit": self.limit,
                "actual": self.actual, "broken": self.broken, "text": self.text}


@dataclass(frozen=True)
class Judged:
    """A finished run as the gate sees it."""
    test_set: str | None
    status: str
    not_met: tuple[str, ...] = ()


def check_test_sets(policy: Policy, runs: Iterable[str], noun: str) -> None:
    """Every test set the policy names must be one the scope runs: a name that isn't
    is a typo, or a set since renamed, and its limit would never apply."""
    runs = sorted(set(runs))
    missing = [name for name in policy.test_sets if name not in runs]
    if missing:
        raise PolicyError(f"the policy names test set{'s' if len(missing) > 1 else ''} "
                          f"{', '.join(map(repr, missing))}, which this {noun} doesn't run "
                          f"(it runs: {', '.join(map(repr, runs)) or 'none'})")


def check_blocking_checks(policy: Policy, labels: Iterable[str], noun: str) -> None:
    """Every blocking check must be a check the scope asks: one that isn't would
    hold whatever happens, a typo or a check since renamed."""
    asked = distinct_labels(sorted(labels, key=str.casefold))
    known = {label.casefold() for label in asked}
    missing = [label for label in policy.blocking_checks if label.casefold() not in known]
    if missing:
        raise PolicyError(f"the policy's blocking check{'s' if len(missing) > 1 else ''} "
                          f"{', '.join(map(repr, missing))} {'are' if len(missing) > 1 else 'is'}"
                          f" no check this {noun} asks (its checks: "
                          f"{', '.join(map(repr, asked)) or 'none'})")


def distinct_labels(labels: Iterable[str]) -> tuple[str, ...]:
    """Each label once, ignoring letter case: as first spelled."""
    first: dict[str, str] = {}
    for label in labels:
        first.setdefault(label.casefold(), label)
    return tuple(first.values())


def _among(label: str, labels: Iterable[str]) -> bool:
    """`label` is one of `labels`, ignoring letter case."""
    return label.casefold() in {other.casefold() for other in labels}


def judge(runs: list[Judged], policy: Policy) -> list[Rule]:
    """Every rule of `policy` on `runs`; the gate passes when none is broken. A run
    fails when its status is in the policy's `fail_on`."""
    rules = [_blocking(label, runs) for label in policy.blocking_checks]
    for name, limits in policy.test_sets.items():
        rules += _limited([run for run in runs if run.test_set == name], limits, policy, name)
    if policy.limits.given:
        rules += _limited(runs, policy.limits, policy, None)
    else:
        # strict unless said otherwise: every run no limit covers must pass
        others = [run for run in runs if run.test_set not in policy.test_sets]
        if others:
            rules.append(_none_allowed(others, policy))
    return rules


def judged_without_not_ran(runs: list[Judged], policy: Policy) -> bool:
    """Whether the gate would pass with the runs that Not Ran left out: then it
    failed only because the application or the judge didn't answer."""
    if "NotRan" not in policy.fail_on:
        return False
    return not any(rule.broken for rule in judge(
        [run for run in runs if run.status != "NotRan"], policy))


def _failures(count: int) -> str:
    return f"{count} failure{'' if count == 1 else 's'}"


def _percent(rate: float, digits: int = 1) -> str:
    return f"{rate * 100:.{digits}f}".rstrip("0").rstrip(".") + "%"


def _percents(rate: float, limit: float) -> tuple[str, str]:
    """The limit as given (99.995%, never rounded to 100%), and the rate to as many
    digits as tell it from the limit, and never 100% or 0% unless it's exactly that:
    never "95% < min 95%", nor "100% passed" with a run failed."""
    least = _percent(limit, 6)
    fewest = max(len(least.rstrip("%").partition(".")[2]), 1)
    shown = _percent(rate, fewest)
    for digits in range(fewest + 1, 10):
        rounded_away = (shown == least and rate != limit) or (
            shown == "100%" and rate < 1) or (shown == "0%" and rate > 0)
        if not rounded_away:
            break
        shown = _percent(rate, digits)
    return shown, least


def _blocking(label: str, runs: list[Judged]) -> Rule:
    failed = sum(_among(label, run.not_met) for run in runs)
    text = (f"blocking check {label!r} failed in {_runs(failed)}" if failed
            else f"blocking check {label!r} held")
    return Rule("blocking_check", None, label, failed, failed > 0,
                f"blocking check {label!r}", text)


def _limited(runs: list[Judged], limits: Limits, policy: Policy,
             test_set: str | None) -> list[Rule]:
    if not runs:
        return []
    where = f"{test_set}: " if test_set else ""
    failed = sum(run.status in policy.fail_on for run in runs)
    rules = []
    if limits.max_failures is not None:
        broken = failed > limits.max_failures
        rules.append(Rule(
            "max_failures", test_set, limits.max_failures, failed, broken,
            f"{where}at most {_failures(limits.max_failures)}",
            f"{where}{_failures(failed)} {'>' if broken else '≤'} max {limits.max_failures}"))
    if limits.min_pass_rate is not None:
        passed = len(runs) - failed
        rate = passed / len(runs)
        broken = rate < limits.min_pass_rate
        shown, least = _percents(rate, limits.min_pass_rate)
        rules.append(Rule(
            "min_pass_rate", test_set, limits.min_pass_rate, rate, broken,
            f"{where}a pass rate of at least {_percent(limits.min_pass_rate)}",
            f"{where}{passed} of {len(runs)} passed ({shown}) "
            f"{'<' if broken else '≥'} min {least}"))
    return rules


def _none_allowed(runs: list[Judged], policy: Policy) -> Rule:
    failed = sum(run.status in policy.fail_on for run in runs)
    where = ""
    if policy.test_sets:
        where = f" outside {', '.join(policy.test_sets)}"
    return Rule("none_allowed", None, 0, failed, failed > 0, f"no failure{where}",
                f"{_failures(failed)}{where}, none allowed")


# -- outcomes --

@dataclass
class Case:
    """One line of the report, and one JUnit test case: a run, or a batch's check."""
    name: str
    group: str
    outcome: str  # passed | failed | error | skipped | tolerated (failing, a limit allowed it)
    message: str = ""


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
    reason: str = ""
    """Why it failed whatever --fail-on says: a stopped or undecided batch, a status
    the CLI doesn't know."""
    rules: list[Rule] = field(default_factory=list)
    """The policy's rules as they held: a run's or an execution's."""
    tolerant: bool = False
    """The policy said more than "any failing run fails": its rules are worth showing."""
    judged: bool = True
    """False when it failed only because runs Not Ran: nothing about quality was decided."""
    unreadable: bool = False
    """The CLI couldn't read it whole: a status it doesn't know, runs listed that
    don't match their counts. Nothing about quality was decided: an error."""

    @property
    def failures(self) -> list[Case]:
        return [case for case in self.cases if case.outcome in ("failed", "error")]

    @property
    def tolerated(self) -> list[Case]:
        """Runs that failed, but within a limit: they fail nothing, and are named."""
        return [case for case in self.cases if case.outcome == "tolerated"]

    @property
    def deciding(self) -> list[Rule]:
        """The rules worth saying: those broken when it failed, else those that
        tolerated a failure."""
        if not self.passed:
            return [rule for rule in self.rules if rule.broken]
        return [rule for rule in self.rules if rule.tolerated]

    def to_json(self) -> dict:
        return {"kind": self.kind, "id": self.id, "scope": self.scope, "status": self.status,
                "passed": self.passed, "judged": self.judged, "unreadable": self.unreadable,
                "summary": self.summary,
                "counts": self.counts, "fail_on": list(self.fail_on),
                "reason": self.reason or None, "gate": [rule.to_json() for rule in self.rules],
                "failures": [_case_json(case) for case in self.failures],
                "tolerated": [_case_json(case) for case in self.tolerated]}


def _case_json(case: Case) -> dict:
    return {"name": case.name, "group": case.group, "outcome": case.outcome,
            "message": case.message}


def run_finished(counts: dict) -> bool:
    """Every run of an execution, by its counts, has finished."""
    return not any(counts.get(status, 0) for status in IN_PROGRESS)


def batch_finished(batch: dict) -> bool:
    return batch.get("result") is not None or batch.get("status") not in IN_PROGRESS


def _verdict(passed: bool) -> str:
    return "PASSED" if passed else "FAILED"


# -- runs and executions --

def within_limits(run: Judged, rules: list[Rule], policy: Policy) -> bool:
    """A failing run that every rule counting it allowed: its test set's own limits
    when it has them, the limits over the whole when there are some, else "no
    failure" outside the limited sets. Reported as passed, so a CI system reading
    the JUnit report doesn't fail the build the gate passed."""
    if not policy.tolerant or run.status not in policy.fail_on:
        return False
    limited = run.test_set in policy.test_sets

    def counts_it(rule: Rule) -> bool:
        if rule.rule == "blocking_check":
            return False  # a blocking check that failed in this run is never tolerated
        if rule.test_set is not None:
            return rule.test_set == run.test_set
        return rule.rule != "none_allowed" or not limited

    return not any(rule.broken for rule in rules if counts_it(rule))


def _run_case(name: str, group: str, status: str, not_met: Iterable[str], error: str | None,
              policy: Policy, allowed: bool = False) -> Case:
    """`allowed`: the rules counting the run held, so it fails nothing."""
    if status in IN_PROGRESS:
        return Case(name, group, "error", f"{status}: not finished when read")
    if status not in RUN_STATUSES:
        return Case(name, group, "error", _unknown(status))
    blocking = [label for label in not_met if _among(label, policy.blocking_checks)]
    if status == "NotRan":
        message = f"Not Ran: {error or 'no reason recorded'}"
    elif status in ("Amber", "Red"):
        message = f"{status}: not met: {', '.join(not_met) or '—'}"
        if blocking:
            message += f" (blocking: {', '.join(blocking)})"
    else:
        message = status
    if status not in policy.fail_on and not blocking:
        return Case(name, group, "passed", "" if status == "Green" else message)
    if allowed and not blocking:
        return Case(name, group, "tolerated", f"{message} (tolerated by the gate)")
    return Case(name, group, "error" if status == "NotRan" else "failed", message)


def _distinct(cases: list[Case], ids: list[str]) -> list[Case]:
    """Cases sharing a group and a name, told apart by their entry's id: CI systems
    key test results on the pair, and would merge them."""
    shared = Counter((case.group, case.name) for case in cases)
    for case, case_id in zip(cases, ids, strict=True):
        if shared[(case.group, case.name)] > 1:
            case.name = f"{case.name} [{case_id}]"
    return cases


def _counts_line(counts: dict) -> str:
    return ", ".join(f"{counts.get(s, 0)} {s}" for s in RUN_STATUSES)


def _runs(count: int) -> str:
    return f"{count} run{'' if count == 1 else 's'}"


def _gated(judged: list[Judged], policy: Policy, reason: str) -> tuple[list[Rule], bool, bool]:
    """The rules, whether it passed, and whether it was judged at all."""
    rules = judge(judged, policy)
    passed = not reason and not any(rule.broken for rule in rules)
    return rules, passed, passed or bool(reason) or not judged_without_not_ran(judged, policy)


def execution_outcome(scope: dict, details: dict, runs: list[dict],
                      policy: Policy = STRICT) -> Outcome:
    """A set's runs are its own; a plan's each carry the test set of their entry."""
    counts = {s: details["runs"].get(s, 0) for s in RUN_STATUSES}
    reported = {s for s, n in details["runs"].items() if n} | {run["status"] for run in runs}
    unknown = sorted(reported - {*RUN_STATUSES, *IN_PROGRESS})
    unfinished = sum(run["status"] in IN_PROGRESS for run in runs)
    reason = "; ".join(why for why in (
        _unknown(", ".join(unknown)) if unknown else "",
        f"{_runs(unfinished)} still in progress when its runs were read" if unfinished
        else runs_disagree(details, runs))
        if why)

    def test_set(run: dict) -> str | None:
        return run.get("test_set_name") if scope["kind"] == "test-plan" else scope["name"]

    def not_met(run: dict) -> tuple[str, ...]:
        return tuple((run.get("checks") or {}).get("not_met", []))

    seen = [Judged(test_set(run), run["status"], not_met(run)) for run in runs]
    rules, passed, judged = _gated(seen, policy, reason)
    cases = _distinct(
        [_run_case(run.get("test_name", run["id"]), test_set(run) or scope["name"],
                   run["status"], not_met(run), run.get("error"), policy,
                   within_limits(one, rules, policy)) for run, one in zip(runs, seen, strict=True)],
        [str((run.get("test_set_entry_id") or {}).get("id") or run["id"]) for run in runs])
    summary = (f"{_verdict(passed)}: {SCOPE_NOUNS[scope['kind']]} {scope['name']!r}, "
               f"{_runs(details.get('run_count', sum(counts.values())))}: "
               f"{_counts_line(counts)}" + (f". {reason}" if reason else ""))
    return Outcome("execution", str(details["id"]), scope, _execution_status(counts), passed,
                   summary, counts, cases, policy.fail_on, reason, rules, policy.tolerant,
                   judged, unreadable=bool(reason))


def runs_disagree(details: dict, runs: list[dict]) -> str:
    """How the runs listed differ from the execution's own counts of them, or "".
    Judged on a list that's short (a page lost) or out of step, the gate would
    pass on runs it never saw."""
    counted = Counter({s: n for s, n in details["runs"].items() if n})
    listed = Counter(run["status"] for run in runs)
    total = details.get("run_count", sum(counted.values()))
    if listed == counted and len(runs) == total:
        return ""
    return (f"its runs as listed ({_tally(listed)}) don't match its counts "
            f"({_runs(total)}: {_tally(counted)})")


def _tally(counts: Counter) -> str:
    return ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "none"


def _execution_status(counts: dict) -> str:
    """The execution's worst outcome, in the API's words."""
    for status in ("NotRan", "Red", "Amber", "Green"):
        if counts.get(status):
            return status
    return "Green"


def run_outcome(scope: dict, details: dict, policy: Policy = STRICT) -> Outcome:
    status = details["status"]
    results = details.get("results") or {}
    # the API's own rule for an execution's runs: a check that errored isn't met
    not_met = tuple(sorted(label for label, r in results.items()
                           if not (r.get("passed") and not r.get("errored"))))
    reason = "" if status in RUN_STATUSES else _unknown(status)
    seen = Judged(None, status, not_met)
    rules, passed, judged = _gated([seen], policy, reason)
    case = _run_case(scope["name"], scope["name"], status, not_met, details.get("error"),
                     policy, within_limits(seen, rules, policy))
    met = len(results) - len(not_met)
    summary = (f"{_verdict(passed)}: test {scope['name']!r}: {status}"
               + (f", {met}/{len(results)} checks met" if results else "")
               + (f" ({details['error']})" if details.get("error") else ""))
    return Outcome("run", str(details["id"]), scope, status, passed, summary,
                   {s: int(s == status) for s in RUN_STATUSES}, [case], policy.fail_on,
                   reason, rules, policy.tolerant, judged, unreadable=bool(reason))


# -- statistics batches --

def batch_outcome(batch: dict, fail_on: tuple = BATCH_FAIL_ON) -> Outcome:
    fail_on = tuple(dict.fromkeys(("fail", *fail_on)))
    status = batch["status"]
    scope = {"kind": batch["scope"]["kind"].replace("_", "-"), "id": str(batch["scope"]["id"]),
             "name": batch["scope"]["name"]}
    result = batch.get("result") or {}
    verdicts = result.get("verdicts") or batch.get("verdicts") or {}
    if status in _BATCH_VERDICT_OF_STATUS:
        why = ""
    else:
        why = _BATCH_ALWAYS_FAIL.get(status) or _unknown(status)
    unreadable = status not in _BATCH_VERDICT_OF_STATUS and status not in _BATCH_ALWAYS_FAIL
    # the status and every check's verdict: an Inconclusive batch whose only
    # undecided check has no verdict, and a Failed batch's inconclusive checks
    passed = not why and _BATCH_VERDICT_OF_STATUS[status] not in fail_on and not any(
        verdicts.get(verdict, 0) for verdict in fail_on)
    # a check with no verdict is what keeps a batch Inconclusive
    undecided_fails = status == "Inconclusive" and "inconclusive" in fail_on
    cases, ids = [], []
    if why:
        cases.append(Case(batch["statistical_test"], scope["name"], "error",
                          f"{status}: {why}"))
        ids.append(str(batch["id"]))
    for entry in result.get("entries", []):
        group = " / ".join(x for x in (entry.get("test_set_name"), entry["name"]) if x)
        for check in entry.get("checks", []):
            cases.append(_check_case(group, check, fail_on, undecided_fails))
            ids.append(str(entry.get("entry_id") or check["label"]))
    sentence = result.get("summary") or batch.get("summary") or why or status
    summary = (f"{_verdict(passed)}: batch of {SCOPE_NOUNS[scope['kind']]} "
               f"{scope['name']!r} ({batch['statistical_test']}): {status}. {sentence}")
    if status == "Done":
        summary += " A trial asks no question: nothing was gated."
    return Outcome("batch", str(batch["id"]), scope, status, passed, summary, verdicts,
                   _distinct(cases, ids), fail_on, why, judged=status != "NotRan",
                   unreadable=unreadable)


def _check_case(group: str, check: dict, fail_on: tuple, undecided_fails: bool) -> Case:
    label = check["label"]
    if not check.get("applies", True):
        return Case(label, group, "skipped", check.get("reason") or "no verdict for this check")
    statistic = check.get("statistic") or {}
    verdict = statistic.get("verdict")
    reason = statistic.get("reason") or check.get("reason") or ""
    message = f"{verdict or 'no verdict'}: {reason}".rstrip(": ")
    if verdict == "pass":
        return Case(label, group, "passed", message)
    if verdict in fail_on or (verdict is None and undecided_fails):
        return Case(label, group, "failed", message)
    return Case(label, group, "skipped", message)
