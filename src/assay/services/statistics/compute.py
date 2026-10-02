"""A finished batch's statistics, computed from its runs (docs/statistics/
dev_notes.md notes 3, 7, 15–17, 21). Pure: the caller loads the runs and the
entries they ran; nothing here touches the database, so every rule is tested
on plain data.

The fold, per entry and per check (by label, the check's identity):
- a run that was Not Ran says nothing about any check: counted apart;
- a check that errored in a run (a judge timeout, no judge chosen) was never
  decided: counted apart, left out of the sample;
- a check missing from a run's results (shouldn't happen: every run of an
  entry scores the same frozen checks) is counted as errored, with the reason;
- the rest is the sample: passed or failed, with a score for scored checks.
"""
import math
from dataclasses import dataclass, field

from assay import stats_math
from assay.models import BatchStatus, TestStatus, TestTypesModel
from assay.schemas import TestTypeAssignment
from assay.schemas.statistics import (
    BatchResult,
    CheckCounts,
    CheckResult,
    CheckVerdict,
    EntryFailures,
    EntryResult,
    FailuresByEntry,
    GateRuleSchema,
    Interval,
    IntervalMethod,
    IntervalSides,
    RunCounts,
    RunStripPoint,
    Scale,
    ScoreSummaryOut,
    SeriesPoint,
    Statistic,
    StatisticalEngine,
)
from assay.services.statistics.catalogue import MAX_TIMES, POWER, percent

NO_RESULT = "No result for this check in this run"


@dataclass(frozen=True)
class BatchRun:
    """One run of a batch, as the statistics read it."""
    id: object
    index: int
    execution_id: object | None
    status: TestStatus
    results: dict | None
    error: str | None


@dataclass
class BatchEntry:
    """One entry of the batch's scope with its runs, in time order."""
    entry_id: object | None
    test_id: object | None
    test_set_id: object | None
    test_set_name: str | None
    name: str
    recorded_answer: bool
    assignments: list[TestTypeAssignment]
    runs: list[BatchRun] = field(default_factory=list)


def r4(value: float | None) -> float | None:
    """Four decimals, the precision every statistics number is returned at."""
    if value is None or math.isnan(value):
        return None
    return round(value, 4)


def run_counts(runs: list[BatchRun]) -> RunCounts:
    counts = RunCounts()
    for run in runs:
        setattr(counts, run.status.value, getattr(counts, run.status.value) + 1)
    return counts


def scale_of(row: TestTypesModel | None) -> Scale | None:
    """A scored type's native range: its threshold field's bounds."""
    if row is None or row.comparison is None:
        return None
    field_ = next((f for f in row.config_fields or [] if f.get("key") == "threshold"), {})
    return Scale(min=field_.get("min"), max=field_.get("max"))


def threshold_of(assignment: TestTypeAssignment) -> float | None:
    raw = (assignment.config or {}).get("threshold")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


JUDGE_ENGINE = "llm_judge"


def applies(name: StatisticalEngine, row: TestTypesModel | None,
            recorded_answer: bool = True) -> tuple[bool, str | None]:
    """Whether a batch test gives a check of this type a verdict, and why not
    (the estimate and the result use this one rule)."""
    if name == StatisticalEngine.one_sample_t and (row is None or row.comparison is None):
        return False, "Pass/fail only: a t-test needs a score on a scale"
    if name == StatisticalEngine.judge_stability:
        if row is None or row.engine != JUDGE_ENGINE:
            return False, "Not an LLM judge: judge stability tests the judge's own consistency"
        if not recorded_answer:
            return False, ("The answer varies between runs: a changed verdict can't be pinned "
                           "on the judge")
    return True, None


# ── one check ────────────────────────────────────────────────────────────────


def _point(run: BatchRun, label: str) -> SeriesPoint:
    common = {"index": run.index, "run_id": run.id, "execution_id": run.execution_id,
              "status": run.status.value}
    if run.status == TestStatus.not_ran:
        return SeriesPoint(**common, passed=None, score=None, error=run.error)
    result = (run.results or {}).get(label)
    if result is None:
        return SeriesPoint(**common, passed=None, score=None, error=NO_RESULT)
    if result.get("errored"):
        return SeriesPoint(**common, passed=None, score=None, error=result.get("detail"))
    return SeriesPoint(**common, passed=bool(result.get("passed")),
                       score=r4(result.get("score")), error=None)


def fold(runs: list[BatchRun], label: str) -> tuple[list[SeriesPoint], CheckCounts]:
    """One check's runs as points, and what they came to."""
    series = [_point(run, label) for run in runs]
    decided = [p for p in series if p.passed is not None]
    passed = sum(p.passed for p in decided)
    return series, CheckCounts(
        evaluated=len(decided), passed=passed, failed=len(decided) - passed,
        errored=sum(p.passed is None and p.status != TestStatus.not_ran.value for p in series),
        not_ran=sum(p.status == TestStatus.not_ran.value for p in series),
    )


def make_interval(lower, point, upper, method: IntervalMethod, level: float,
              sides: IntervalSides) -> Interval:
    return Interval(lower=r4(lower), point=r4(point), upper=r4(upper), method=method,
                    level=level, sides=sides)


def _decide_message(times: int | None, side: str) -> str | None:
    if times is None:
        return None
    message = f"A new batch of about {times} times would likely prove it {side}"
    if times > MAX_TIMES:
        message += f", more than one batch can run ({MAX_TIMES})"
    return message + "."


def _gate(passed: int, n: int, target: float, confidence: float) -> Statistic:
    gate = stats_math.binomial_gate(passed, n, target, confidence)
    rule = GateRuleSchema(times=n, pass_at_least=gate.rule.pass_at_least,
                          fail_at_most=gate.rule.fail_at_most)
    interval = make_interval(gate.lower, passed / n, gate.upper, IntervalMethod.exact,
                         confidence, IntervalSides.one)
    sure, goal = percent(confidence), percent(target)
    times_to_decide = message = None
    if gate.verdict == stats_math.Verdict.passed:
        reason = f"{sure} confident it passes at least {goal} of the time ({passed} of {n})."
    elif gate.verdict == stats_math.Verdict.failed:
        reason = f"{sure} confident it passes less than {goal} of the time ({passed} of {n})."
    else:
        reason = (f"Not proven either way at {n} runs: {passed} of {n} passed, against a "
                  f"target of {goal}.")
        times_to_decide = stats_math.binomial_gate_runs_to_decide(passed, n, target, confidence)
        rate = passed / n
        side = f"at least {goal}" if rate > target else f"below {goal}"
        message = _decide_message(times_to_decide, side)
        if times_to_decide is None:
            message = (f"It passed exactly {goal} of the time: no batch size would likely "
                       "decide it.")
    return Statistic(
        verdict=CheckVerdict(gate.verdict.value), reason=reason, n=n, interval=interval,
        p_value_pass=r4(gate.p_value_pass), p_value_fail=r4(gate.p_value_fail), rule=rule,
        times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def _t_test(scores: list[float], threshold: float, higher_is_better: bool,
            confidence: float) -> Statistic:
    test = stats_math.one_sample_t(scores, threshold, higher_is_better, confidence)
    n, sure = test.n, percent(confidence)
    passing, failing = ("at least", "below") if higher_is_better else ("at most", "above")
    interval = (None if test.lower is None else
                make_interval(test.lower, test.mean, test.upper, IntervalMethod.t, confidence,
                          IntervalSides.one))
    times_to_decide = message = None
    if test.verdict == stats_math.Verdict.passed:
        reason = (f"{sure} confident the mean score is {passing} the threshold {threshold:g} "
                  f"(mean {test.mean:.4g} over {n} runs).")
    elif test.verdict == stats_math.Verdict.failed:
        reason = (f"{sure} confident the mean score is {failing} the threshold {threshold:g} "
                  f"(mean {test.mean:.4g} over {n} runs).")
    elif n == 1:
        reason = "One score can't be tested: there's no spread to measure. Run a new batch."
    else:
        reason = (f"Not proven either way at {n} runs: mean {test.mean:.4g} against the "
                  f"threshold {threshold:g}.")
        times_to_decide = stats_math.one_sample_t_runs_to_decide(
            test.mean, test.sd or 0.0, threshold, confidence, POWER)
        on_passing_side = test.mean >= threshold if higher_is_better else (
            test.mean <= threshold)
        side = (f"{passing if on_passing_side else failing} the threshold {threshold:g}")
        message = _decide_message(times_to_decide, side)
        if times_to_decide is None:
            message = ("The mean is exactly the threshold: no batch size would likely "
                       "decide it.")
    if test.sd == 0 and n > 1:
        reason = reason[:-1] + f" — every score was {test.mean:g}, so there's no spread."
    return Statistic(
        verdict=CheckVerdict(test.verdict.value), reason=reason, n=n, interval=interval,
        p_value_pass=r4(test.p_value_pass), p_value_fail=r4(test.p_value_fail), rule=None,
        t=r4(test.t), df=test.df, standard_error=r4(test.standard_error),
        times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def _agreement(passed: int, n: int, target: float, confidence: float) -> Statistic:
    """Judge stability: the gate on the runs that gave the judge's usual
    verdict (its majority: pass or fail)."""
    agreeing = max(passed, n - passed)
    usual = "pass" if passed >= n - passed else "fail"
    gate = stats_math.binomial_gate(agreeing, n, target, confidence)
    rule = GateRuleSchema(times=n, pass_at_least=gate.rule.pass_at_least,
                          fail_at_most=gate.rule.fail_at_most)
    interval = make_interval(gate.lower, agreeing / n, gate.upper, IntervalMethod.exact,
                             confidence, IntervalSides.one)
    sure, goal = percent(confidence), percent(target)
    told = f"{agreeing} of {n} runs said {usual}"
    times_to_decide = message = None
    if gate.verdict == stats_math.Verdict.passed:
        reason = (f"{sure} confident the judge agrees with itself at least {goal} of the "
                  f"time ({told}).")
    elif gate.verdict == stats_math.Verdict.failed:
        reason = (f"{sure} confident the judge agrees with itself less than {goal} of the "
                  f"time ({told}).")
    else:
        reason = f"Not proven either way at {n} runs: {told}, against a target of {goal}."
        times_to_decide = stats_math.binomial_gate_runs_to_decide(agreeing, n, target,
                                                                  confidence)
        side = f"at least {goal}" if agreeing / n > target else f"below {goal}"
        message = _decide_message(times_to_decide, side)
    return Statistic(
        verdict=CheckVerdict(gate.verdict.value), reason=reason, n=n, interval=interval,
        p_value_pass=r4(gate.p_value_pass), p_value_fail=r4(gate.p_value_fail), rule=rule,
        times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def _no_verdict(reason: str, n: int) -> Statistic:
    return Statistic(verdict=None, reason=reason, n=n, interval=None, p_value_pass=None,
                     p_value_fail=None, rule=None, times_to_decide=None,
                     times_to_decide_message=None)


def check_result(name: StatisticalEngine, parameters: dict[str, float], floor: int,
                 stopped: bool, assignment: TestTypeAssignment, row: TestTypesModel | None,
                 runs: list[BatchRun], recorded_answer: bool = True) -> CheckResult:
    confidence = parameters["confidence"]
    series, counts = fold(runs, assignment.label)
    decided = [p for p in series if p.passed is not None]
    passed = counts.passed
    is_gate = name == StatisticalEngine.binomial_gate
    is_stability = name == StatisticalEngine.judge_stability
    target = parameters["target"] if is_gate or is_stability else None

    pass_rate = None
    if decided:
        n = len(decided)
        if is_gate:
            pass_rate = make_interval(
                stats_math.exact_lower_bound(passed, n, confidence), passed / n,
                stats_math.exact_upper_bound(passed, n, confidence),
                IntervalMethod.exact, confidence, IntervalSides.one)
        else:
            lower, upper = stats_math.wilson_interval(passed, n, confidence)
            pass_rate = make_interval(lower, passed / n, upper, IntervalMethod.wilson, confidence,
                                  IntervalSides.two)

    scale = scale_of(row)
    threshold = threshold_of(assignment) if scale else None
    scores = [p.score for p in decided if p.score is not None]
    summary = None
    if scale and scores:
        s = stats_math.summarize_scores(scores)
        summary = ScoreSummaryOut(n=s.n, mean=r4(s.mean), sd=r4(s.sd), min=r4(s.minimum),
                                  max=r4(s.maximum), p10=r4(s.p10), p25=r4(s.p25),
                                  median=r4(s.median), p75=r4(s.p75), p90=r4(s.p90))

    does_apply, why_not = applies(name, row, recorded_answer)
    statistic = agreement = None
    if does_apply:
        n = len(scores) if name == StatisticalEngine.one_sample_t else len(decided)
        if n == 0:
            statistic = _no_verdict(
                "No run decided this check: every one errored or was Not Ran.", 0)
        elif stopped and n < floor:
            statistic = _no_verdict(
                f"The batch was stopped with {n} evaluated "
                f"{'run' if n == 1 else 'runs'}, below the {floor} this test needs: no "
                "verdict. The rate and range above describe what ran.", n)
        elif is_gate:
            statistic = _gate(passed, n, target, confidence)
        elif is_stability:
            statistic = _agreement(passed, n, target, confidence)
            agreement = statistic.interval
        elif threshold is None:
            statistic = _no_verdict(
                "The check's threshold isn't a number: there's nothing to test the mean "
                "against.", n)
        else:
            statistic = _t_test(scores, threshold, row.comparison.value == "gte", confidence)

    return CheckResult(
        label=assignment.label, test_type=assignment.name, applies=does_apply,
        reason=why_not, scale=scale, threshold=threshold,
        comparison=row.comparison.value if scale else None, target=target, counts=counts,
        pass_rate=pass_rate, agreement=agreement, scores=summary, statistic=statistic,
        series=series,
    )


# ── the batch ────────────────────────────────────────────────────────────────


def strip(runs: list[BatchRun]) -> list[RunStripPoint]:
    return [RunStripPoint(index=run.index, run_id=run.id, execution_id=run.execution_id,
                          status=run.status.value) for run in runs]


def run_passed(run: BatchRun) -> bool | None:
    """A run's outcome over its decided checks: True when every one passed,
    False when one failed, None when it decided nothing (Not Ran, or every
    check errored) — errored checks aren't failures of the application."""
    if run.status == TestStatus.not_ran:
        return None
    decided = [r for r in (run.results or {}).values() if not r.get("errored")]
    if not decided:
        return None
    return all(r.get("passed") for r in decided)


def failures_by_entry(entries: list[BatchEntry], confidence: float) -> FailuresByEntry | None:
    """Do failures concentrate in some entries? A k×2 chi-square on entries ×
    (passed, failed) runs. None for a single entry."""
    if len(entries) < 2:
        return None
    rows = []
    for entry in entries:
        outcomes = [o for o in (run_passed(run) for run in entry.runs) if o is not None]
        rows.append(EntryFailures(entry_id=entry.entry_id, name=entry.name,
                                  test_set_name=entry.test_set_name,
                                  passed=sum(outcomes), failed=len(outcomes) - sum(outcomes)))
    counted = [row for row in rows if row.passed + row.failed]
    worst = sorted(rows, key=lambda r: (-(r.failed / (r.passed + r.failed or 1)), r.name))
    failed = sum(r.failed for r in counted)
    empty = FailuresByEntry(verdict=None, reason="", chi_square=None, df=None, p_value=None,
                            approximate=False, entries=worst)
    if len(counted) < 2:
        empty.reason = "Fewer than two entries had a run that decided anything."
        return empty
    if failed == 0:
        empty.reason = "No run failed: there are no failures to locate."
        return empty
    if failed == sum(r.passed + r.failed for r in counted):
        empty.reason = "Every run failed: the failures are everywhere."
        return empty
    test = stats_math.chi_square_kx2([(r.passed, r.failed) for r in counted])
    approximate = test.min_expected < 5
    if test.p_value <= 1 - confidence:
        names = [r.name for r in worst if r.failed][:3]
        verdict = "concentrated"
        reason = (f"Failures concentrate in some entries (p = {test.p_value:.4g}): most in "
                  f"{', '.join(names)}.")
    else:
        verdict = "no_evidence"
        reason = (f"No evidence that failures concentrate in particular entries "
                  f"(p = {test.p_value:.4g}).")
    if approximate:
        reason = reason[:-1] + " — some expected counts are under 5, so p is approximate."
    return FailuresByEntry(verdict=verdict, reason=reason, chi_square=r4(test.chi_square),
                           df=test.df, p_value=r4(test.p_value), approximate=approximate,
                           entries=worst)


def compute(name: StatisticalEngine, parameters: dict[str, float], floor: int,
            stopped: bool, entries: list[BatchEntry], types: dict[str, TestTypesModel],
            computed_at) -> BatchResult:
    """The result of a batch none of whose runs is Pending or Running."""
    results = []
    for entry in entries:
        checks = [check_result(name, parameters, floor, stopped, assignment,
                               types.get(assignment.name), entry.runs, entry.recorded_answer)
                  for assignment in entry.assignments]
        results.append(EntryResult(
            entry_id=entry.entry_id, test_id=entry.test_id, test_set_id=entry.test_set_id,
            test_set_name=entry.test_set_name, name=entry.name,
            recorded_answer=entry.recorded_answer, runs=run_counts(entry.runs),
            checks=checks, strip=strip(entry.runs),
        ))
    verdicts = {"pass": 0, "fail": 0, "inconclusive": 0, "none": 0}
    checks_total = 0
    for entry in results:
        for check in entry.checks:
            checks_total += 1
            if check.statistic is not None:
                verdict = check.statistic.verdict
                verdicts[verdict.value if verdict else "none"] += 1
    return BatchResult(
        computed_at=computed_at, checks_total=checks_total,
        checks_applicable=sum(verdicts.values()), verdicts=verdicts, summary="",
        entries=results,
        failures_by_entry=failures_by_entry(entries, parameters["confidence"]),
    )


def roll_up(result: BatchResult, stopped: bool, runs: list[BatchRun]) -> BatchStatus:
    """The batch's outcome (note 17): stopped → Incomplete; nothing evaluated →
    NotRan; one proven failure fails it; every applicable check proven passes
    it; otherwise Inconclusive."""
    if stopped:
        return BatchStatus.incomplete
    if all(run.status == TestStatus.not_ran for run in runs):
        return BatchStatus.not_ran
    if result.verdicts["fail"]:
        return BatchStatus.failed
    if result.verdicts["pass"] == result.checks_applicable:
        return BatchStatus.passed
    return BatchStatus.inconclusive


def summary(status: BatchStatus, result: BatchResult, times_ran: int, times_requested: int,
            runs: list[BatchRun]) -> str:
    """The outcome in one sentence, for lists and the batch page's header.
    `times_ran`: the times not wholly cancelled by a stop."""
    verdicts, applicable = result.verdicts, result.checks_applicable
    checks = f"{applicable} {'check' if applicable == 1 else 'checks'}"
    if status == BatchStatus.passed:
        return (f"Passed: every one of the {checks} is proven." if applicable > 1
                else "Passed: the check is proven.")
    if status == BatchStatus.failed:
        return f"Failed: {verdicts['fail']} of {checks} proven to fail."
    if status == BatchStatus.not_ran:
        first = next((run.error for run in runs if run.error), None)
        return "Not Ran: no run could be evaluated" + (f" — {first}" if first else ".")
    parts = [f"{verdicts['pass']} proven", f"{verdicts['inconclusive']} undecided"]
    if verdicts["none"]:
        parts.append(f"{verdicts['none']} without a verdict")
    tally = f"{', '.join(parts)} of {checks}"
    if status == BatchStatus.incomplete:
        if times_ran == 0:
            return "Incomplete: stopped before any run ran."
        return (f"Incomplete: stopped after {times_ran} of {times_requested} times ran; "
                f"{tally}.")
    return f"Inconclusive: {tally}; a bigger batch would decide the rest."
