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

from assay import sequential, stats_math
from assay.assignment_labels import in_label_order, labelled
from assay.batch_waves import read_overrides
from assay.models import JUDGE_ENGINE, BatchStatus, TestStatus, TestTypesModel
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
from assay.services.statistics.catalogue import MAX_TIMES, POWER, often, sure

NO_RESULT = "No result for this check in this run"
LEFT_OUT = "Left out of this batch"
TRIAL_REASON = "A trial gives no verdict: it shows how often the check passes"


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


def frozen_assignments(frozen: list[dict] | None) -> list[TestTypeAssignment]:
    """A frozen copy's checks (a set entry's, a standalone run's), labelled and
    in label order."""
    return in_label_order(labelled([TestTypeAssignment(**item) for item in frozen or []]))


def set_entry(entry, set_name: str | None) -> BatchEntry:
    """A test set entry (TestSetEntryModel) as statistics read it, without its
    runs: the estimate and the batch build their entries here, alike."""
    return BatchEntry(
        entry_id=entry.id, test_id=entry.test_id, test_set_id=entry.test_set_id,
        test_set_name=set_name, name=entry.name,
        recorded_answer=entry.model_output is not None,
        assignments=frozen_assignments(entry.test_type_assignments),
    )


def entry_order(entry: BatchEntry) -> tuple[str, str, str]:
    """The one order entries are shown in everywhere in statistics: by set
    name, then entry name (letter case aside), then id."""
    return ((entry.test_set_name or "").casefold(), entry.name.casefold(),
            str(entry.entry_id))


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



def applies(name: StatisticalEngine, row: TestTypesModel | None,
            recorded_answer: bool = True) -> tuple[bool, str | None]:
    """Whether a batch test gives a check of this type a verdict, and why not
    (the estimate and the result use this one rule)."""
    if name == StatisticalEngine.trial:
        return False, TRIAL_REASON
    if name in (StatisticalEngine.one_sample_t, StatisticalEngine.sequential_t) and (
            row is None or row.comparison is None):
        return False, "It only passes or fails: an average needs a check that gives a score"
    if name in (StatisticalEngine.judge_stability, StatisticalEngine.sequential_judge_stability):
        if row is None or row.engine != JUDGE_ENGINE:
            return False, "Not an LLM judge: this test checks whether the judge is consistent"
        if not recorded_answer:
            return False, ("The answer changes from run to run, so a changed verdict could be "
                           "the answer's doing, not the judge's")
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


def _decide(times: int | None, floor: int, leaning: str) -> tuple[int | None, str]:
    """The size of a new batch that would likely decide a check, and the
    sentence to show: `leaning` is what the batch so far points to ("it passes
    at least 9 times in 10"). Never below the floor — a smaller batch would be
    refused at creation — and None past the most a batch can run, which the
    sentence says instead of a number nobody can run."""
    if times is None:
        return None, (f"Showing that {leaning} would take more than {MAX_TIMES} times, more "
                      f"than one batch can run.")
    times = max(times, floor)
    return times, f"A new batch of about {times} times would likely show that {leaning}."


def _runs(n: int) -> str:
    return f"{n} {'run' if n == 1 else 'runs'}"


_ON_THE_LINE = "right on the line, so no number of runs would settle it."


def _gate(passed: int, n: int, target: float, confidence: float, floor: int) -> Statistic:
    gate = stats_math.binomial_gate(passed, n, target, confidence)
    rule = GateRuleSchema(times=n, pass_at_least=gate.rule.pass_at_least,
                          fail_at_most=gate.rule.fail_at_most)
    interval = make_interval(gate.lower, passed / n, gate.upper, IntervalMethod.exact,
                         confidence, IntervalSides.one)
    goal = often(target)
    times_to_decide = message = None
    if gate.verdict == stats_math.Verdict.passed:
        reason = (f"Passed {passed} of {_runs(n)}: it passes at least {goal} "
                  f"({sure(confidence)}).")
    elif gate.verdict == stats_math.Verdict.failed:
        record = (f"Failed all {_runs(n)}" if passed == 0
                  else f"Passed only {passed} of {_runs(n)}")
        reason = f"{record}: it passes less than {goal} ({sure(confidence)})."
    else:
        reason = (f"Can't tell yet: it passed {passed} of {_runs(n)}. That's close to {goal}, "
                  f"and {_runs(n)} aren't enough to know which side it's on.")
        if passed / n == target:
            message = f"It passed exactly {goal}: {_ON_THE_LINE}"
        else:
            leaning = (f"it passes at least {goal}" if passed / n > target
                       else f"it passes less than {goal}")
            times_to_decide, message = _decide(stats_math.binomial_gate_runs_to_decide(
                passed, n, target, confidence, limit=MAX_TIMES), floor, leaning)
    return Statistic(
        verdict=CheckVerdict(gate.verdict.value), reason=reason, n=n, interval=interval,
        p_value_pass=r4(gate.p_value_pass), p_value_fail=r4(gate.p_value_fail), rule=rule,
        times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def _t_test(scores: list[float], threshold: float, higher_is_better: bool,
            confidence: float, floor: int) -> Statistic:
    test = stats_math.one_sample_t(scores, threshold, higher_is_better, confidence)
    n = test.n
    passing, failing = ("above", "below") if higher_is_better else ("below", "above")
    average = f"{test.mean:.3g}" if test.mean is not None else ""
    interval = (None if test.lower is None else
                make_interval(test.lower, test.mean, test.upper, IntervalMethod.t, confidence,
                          IntervalSides.one))
    times_to_decide = message = None
    if test.verdict == stats_math.Verdict.passed:
        reason = (f"Average score {average} over {_runs(n)}: safely {passing} the "
                  f"{threshold:g} needed ({sure(confidence)}).")
    elif test.verdict == stats_math.Verdict.failed:
        reason = (f"Average score {average} over {_runs(n)}: {failing} the {threshold:g} "
                  f"needed ({sure(confidence)}).")
    elif n == 1:
        reason = "One score isn't enough to judge an average. Run a new batch."
    else:
        reason = (f"Can't tell yet: the average score is {average} over {_runs(n)}, close to "
                  f"the {threshold:g} needed, and the scores vary too much to know which side "
                  f"it's on.")
        if test.mean == threshold:
            message = f"The average is exactly the threshold: {_ON_THE_LINE}"
        else:
            on_passing_side = test.mean >= threshold if higher_is_better else (
                test.mean <= threshold)
            leaning = (f"the average is {passing if on_passing_side else failing} the "
                       f"{threshold:g} needed")
            times_to_decide, message = _decide(stats_math.one_sample_t_runs_to_decide(
                test.mean, test.sd or 0.0, threshold, confidence, POWER, limit=MAX_TIMES),
                floor, leaning)
    if test.sd == 0 and n > 1:
        reason = reason[:-1] + f" — every run scored exactly {average}."
    return Statistic(
        verdict=CheckVerdict(test.verdict.value), reason=reason, n=n, interval=interval,
        p_value_pass=r4(test.p_value_pass), p_value_fail=r4(test.p_value_fail), rule=None,
        t=r4(test.t), df=test.df, standard_error=r4(test.standard_error),
        times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def _agreement(passed: int, n: int, target: float, confidence: float, floor: int) -> Statistic:
    """Judge stability: the gate on the runs that gave the judge's usual
    verdict (its majority: pass or fail)."""
    agreeing = max(passed, n - passed)
    usual = "pass" if passed >= n - passed else "fail"
    gate = stats_math.binomial_gate(agreeing, n, target, confidence)
    rule = GateRuleSchema(times=n, pass_at_least=gate.rule.pass_at_least,
                          fail_at_most=gate.rule.fail_at_most)
    interval = make_interval(gate.lower, agreeing / n, gate.upper, IntervalMethod.exact,
                             confidence, IntervalSides.one)
    goal = often(target)
    told = f"{agreeing} of {_runs(n)} said {usual}"
    times_to_decide = message = None
    if gate.verdict == stats_math.Verdict.passed:
        reason = (f"The judge is consistent: {told}. It agrees with itself at least {goal} "
                  f"({sure(confidence)}).")
    elif gate.verdict == stats_math.Verdict.failed:
        reason = (f"The judge is inconsistent: {told}. It agrees with itself less than "
                  f"{goal} ({sure(confidence)}).")
    else:
        reason = (f"Can't tell yet: {told}. That's close to {goal}, and {_runs(n)} aren't "
                  f"enough to know which side it's on.")
        if agreeing / n == target:
            message = f"It agreed exactly {goal}: {_ON_THE_LINE}"
        else:
            leaning = (f"it agrees with itself at least {goal}" if agreeing / n > target
                       else f"it agrees with itself less than {goal}")
            times_to_decide, message = _decide(stats_math.binomial_gate_runs_to_decide(
                agreeing, n, target, confidence, limit=MAX_TIMES), floor, leaning)
    return Statistic(
        verdict=CheckVerdict(gate.verdict.value), reason=reason, n=n, interval=interval,
        p_value_pass=r4(gate.p_value_pass), p_value_fail=r4(gate.p_value_fail), rule=rule,
        times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def _sequential(series: list[SeriesPoint], plan: sequential.WavePlan, target: float,
                confidence: float, stopped: bool, agreement: bool) -> Statistic:
    """Until there's an answer: the first look that decided the check, at the
    look's stricter level, and the numbers as they were then."""
    outcomes = [(p.index, bool(p.passed)) for p in series if p.passed is not None]
    found = sequential.decide(outcomes, plan, target, agreement=agreement)
    counted, n = found.passes, found.evaluated
    gate = stats_math.binomial_gate(counted, n, target, plan.level)
    goal = often(target)
    if agreement:
        usual = "pass" if sum(o[1] for o in outcomes[:n]) * 2 >= n else "fail"
        told = f"{counted} of its first {_runs(n)} said {usual}"
        if found.verdict == "pass":
            reason = (f"The judge is consistent: {told}. It agrees with itself at least "
                      f"{goal} ({sure(confidence)}).")
        elif found.verdict == "fail":
            reason = (f"The judge is inconsistent: {told}. It agrees with itself less than "
                      f"{goal} ({sure(confidence)}).")
    elif found.verdict == "pass":
        reason = (f"Passed {counted} of its first {_runs(n)}: it passes at least {goal} "
                  f"({sure(confidence)}).")
    elif found.verdict == "fail":
        record = (f"Failed all of its first {_runs(n)}" if counted == 0
                  else f"Passed only {counted} of its first {_runs(n)}")
        reason = f"{record}: it passes less than {goal} ({sure(confidence)})."
    if found.verdict is None:
        reason = (f"The batch was stopped before this check had its answer ({counted} of "
                  f"{_runs(n)})." if stopped else
                  f"Can't tell yet: {counted} of {_runs(n)}, the most this batch could run, "
                  f"is too close to {goal}. A new batch with a higher maximum might settle "
                  "it.")
    interval = make_interval(gate.lower, counted / n if n else None, gate.upper,
                             IntervalMethod.exact, round(plan.level, 4), IntervalSides.one)
    verdict = {"pass": CheckVerdict.passed, "fail": CheckVerdict.failed}.get(
        found.verdict, CheckVerdict.inconclusive)
    rule = stats_math.binomial_gate_rule(n, target, plan.level)
    return Statistic(
        verdict=verdict, reason=reason, n=n, interval=interval,
        p_value_pass=r4(gate.p_value_pass), p_value_fail=r4(gate.p_value_fail),
        rule=GateRuleSchema(times=n, pass_at_least=rule.pass_at_least,
                            fail_at_most=rule.fail_at_most),
        times_to_decide=None, times_to_decide_message=None)


def _sequential_mean(series: list[SeriesPoint], plan: sequential.WavePlan,
                     threshold: float | None, higher_is_better: bool, stopped: bool,
                     confidence: float) -> Statistic:
    """Until there's an answer, for an average: the first look whose t-test
    decided it, at the looks' stricter level, and the numbers then."""
    if threshold is None:
        return _no_verdict("This check's threshold isn't a number, so there's nothing to "
                           "compare the average with.", 0)
    scores = [(p.index, p.score) for p in series if p.passed is not None and p.score is not None]
    verdict, _, test = sequential.decide_mean(scores, plan, threshold, higher_is_better)
    if test is None:
        return _no_verdict("Fewer than two scores came back: nothing to judge an average "
                           "from.", len(scores))
    n = test.n
    passing, failing = ("above", "below") if higher_is_better else ("below", "above")
    average = f"{test.mean:.3g}"
    if verdict == "pass":
        reason = (f"Average score {average} over its first {_runs(n)}: safely {passing} the "
                  f"{threshold:g} needed ({sure(confidence)}).")
    elif verdict == "fail":
        reason = (f"Average score {average} over its first {_runs(n)}: {failing} the "
                  f"{threshold:g} needed ({sure(confidence)}).")
    elif stopped:
        reason = (f"The batch was stopped before this average had its answer (average "
                  f"{average} over {_runs(n)}).")
    else:
        reason = (f"Can't tell yet: an average of {average} over {_runs(n)}, the most this "
                  f"batch could run, is too close to the {threshold:g} needed. A new batch "
                  "with a higher maximum might settle it.")
    interval = (None if test.lower is None else make_interval(
        test.lower, test.mean, test.upper, IntervalMethod.t, round(plan.level, 4),
        IntervalSides.one))
    return Statistic(
        verdict={"pass": CheckVerdict.passed, "fail": CheckVerdict.failed}.get(
            verdict, CheckVerdict.inconclusive),
        reason=reason, n=n, interval=interval, p_value_pass=r4(test.p_value_pass),
        p_value_fail=r4(test.p_value_fail), rule=None, t=r4(test.t), df=test.df,
        standard_error=r4(test.standard_error), times_to_decide=None,
        times_to_decide_message=None)


def _no_verdict(reason: str, n: int) -> Statistic:
    return Statistic(verdict=None, reason=reason, n=n, interval=None, p_value_pass=None,
                     p_value_fail=None, rule=None, times_to_decide=None,
                     times_to_decide_message=None)


def check_result(name: StatisticalEngine, parameters: dict[str, float], floor: int,
                 stopped: bool, assignment: TestTypeAssignment, row: TestTypesModel | None,
                 runs: list[BatchRun], recorded_answer: bool = True,
                 target: float | None = None, left_out: bool = False,
                 waves: dict | None = None) -> CheckResult:
    """One check's result. `target` is its own target when the batch set one
    (else the batch's); `left_out` marks a check the batch's runs skipped;
    `waves` is a batch's wave plan when it ran until there's an answer."""
    confidence = parameters.get("confidence", 0.95)
    series, counts = fold(runs, assignment.label)
    decided = [p for p in series if p.passed is not None]
    passed = counts.passed
    is_sequential = name in (StatisticalEngine.sequential_gate,
                             StatisticalEngine.sequential_judge_stability)
    is_gate = name in (StatisticalEngine.binomial_gate, StatisticalEngine.sequential_gate)
    is_stability = name in (StatisticalEngine.judge_stability,
                            StatisticalEngine.sequential_judge_stability)
    if is_gate or is_stability:
        target = target if target is not None else parameters["target"]
        floor = stats_math.binomial_floor(target, confidence)
    else:
        target = None

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
    if left_out:
        does_apply, why_not = False, LEFT_OUT
    statistic = agreement = None
    if does_apply:
        n = len(scores) if name == StatisticalEngine.one_sample_t else len(decided)
        if n == 0:
            statistic = _no_verdict(
                "No run gave this check a result: the check couldn't run, or the run was Not "
                "Ran.", 0)
        elif stopped and n < floor and not is_sequential:
            statistic = _no_verdict(
                f"The batch was stopped after {_runs(n)} with a result, fewer than the "
                f"{floor} this test needs to give an answer. The numbers above show what "
                "did run.", n)
        elif name == StatisticalEngine.one_sample_t and threshold is None:
            statistic = _no_verdict(
                "This check's threshold isn't a number, so there's nothing to compare the "
                "average with.", n)
        elif name == StatisticalEngine.one_sample_t and n < floor:
            # the t-test's floor is a rule of thumb about how well the spread is
            # known, true of any sample however it got small; the gate's floor
            # is only the least that can *pass*, and a proven failure below it
            # is still proven
            statistic = _no_verdict(
                f"Only {n} {'score' if n == 1 else 'scores'} came back, fewer than the "
                f"{floor} needed to judge an average. The other runs were Not Ran or "
                "couldn't run the check; the scores above show what did.", n)
        elif name == StatisticalEngine.sequential_t:
            statistic = _sequential_mean(series, sequential.WavePlan.from_json(waves),
                                         threshold, row.comparison.value == "gte", stopped,
                                         confidence)
        elif is_sequential:
            plan = sequential.WavePlan.from_json(waves)
            if target != parameters["target"]:
                plan = sequential.WavePlan(looks=plan.looks, level=sequential._calibrate(
                    plan.looks, target, confidence))
            statistic = _sequential(series, plan, target, confidence, stopped, is_stability)
            if is_stability:
                agreement = statistic.interval
        elif is_gate:
            statistic = _gate(passed, n, target, confidence, floor)
        elif is_stability:
            statistic = _agreement(passed, n, target, confidence, floor)
            agreement = statistic.interval
        else:
            statistic = _t_test(scores, threshold, row.comparison.value == "gte", confidence,
                                floor)

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
        empty.reason = "Fewer than two entries gave a result: nothing to compare between them."
        return empty
    if failed == 0:
        empty.reason = "No run failed: there are no failures to look into."
        return empty
    if failed == sum(r.passed + r.failed for r in counted):
        empty.reason = "Every run failed, in every entry."
        return empty
    test = stats_math.chi_square_kx2([(r.passed, r.failed) for r in counted])
    approximate = test.min_expected < 5
    if test.p_value <= 1 - confidence:
        names = [r.name for r in worst if r.failed][:3]
        verdict = "concentrated"
        reason = f"Most failures come from a few entries: {', '.join(names)}."
    else:
        verdict = "no_evidence"
        reason = "Failures are spread across the entries: no entry stands out."
    return FailuresByEntry(verdict=verdict, reason=reason, chi_square=r4(test.chi_square),
                           df=test.df, p_value=r4(test.p_value), approximate=approximate,
                           entries=worst)


def compute(name: StatisticalEngine, parameters: dict[str, float], floor: int,
            stopped: bool, entries: list[BatchEntry], types: dict[str, TestTypesModel],
            computed_at, overrides: dict | None = None,
            waves: dict | None = None) -> BatchResult:
    """The result of a batch none of whose runs is Pending or Running.
    `overrides` is the batch's per-check targets and left-out checks, as
    stored."""
    targets, left_out = read_overrides(overrides)
    results = []
    for entry in entries:
        checks = [check_result(name, parameters, floor, stopped, assignment,
                               types.get(assignment.name), entry.runs, entry.recorded_answer,
                               target=targets.get((entry.entry_id, assignment.label)),
                               left_out=(entry.entry_id, assignment.label) in left_out,
                               waves=waves)
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
        failures_by_entry=failures_by_entry(entries, parameters.get("confidence", 0.95)),
    )


def roll_up(result: BatchResult, stopped: bool, runs: list[BatchRun],
            engine: StatisticalEngine | None = None) -> BatchStatus:
    """The batch's outcome (note 17): stopped → Incomplete; nothing evaluated —
    every run Not Ran, or every applicable check errored in every run (no
    judge chosen, an engine not installed), so not one verdict could be drawn
    → NotRan, since no batch size would change it; one proven failure fails
    it; every applicable check proven passes it; otherwise Inconclusive."""
    if stopped:
        return BatchStatus.incomplete
    if all(run.status == TestStatus.not_ran for run in runs):
        return BatchStatus.not_ran
    if engine == StatisticalEngine.trial:
        return BatchStatus.done  # it asked no question
    if result.checks_applicable and result.verdicts["none"] == result.checks_applicable:
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
    if status == BatchStatus.done:
        rates = [f"{c.label} {c.counts.passed} of {c.counts.evaluated}"
                 for e in result.entries for c in e.checks if c.counts.evaluated]
        shown = "; ".join(rates[:4]) + ("; …" if len(rates) > 4 else "")
        return (f"Done: ran {times_ran} {'time' if times_ran == 1 else 'times'}"
                + (f" — passed: {shown}." if shown else "."))
    if status == BatchStatus.passed:
        if applicable == 1:
            return "Passed: the check met the goal."
        return ("Passed: both checks met the goal." if applicable == 2
                else f"Passed: all {applicable} checks met the goal.")
    if status == BatchStatus.failed:
        if applicable == 1:
            return "Failed: the check fell short of the goal."
        return f"Failed: {verdicts['fail']} of the {applicable} checks fell short of the goal."
    if status == BatchStatus.not_ran:
        if all(run.status == TestStatus.not_ran for run in runs):
            first = next((run.error for run in runs if run.error), None)
            return "Not Ran: no run could be carried out" + (f" — {first}" if first else ".")
        first = next((r.get("detail") for run in runs for r in (run.results or {}).values()
                      if r.get("errored") and r.get("detail")), None)
        return ("Not Ran: no check gave a result — each one errored in every run"
                + (f" ({first})" if first else "") + ". Fix that first: running more times "
                "won't help.")
    tally = _tally(verdicts, applicable)
    if status == BatchStatus.incomplete:
        if times_ran == 0:
            return "Incomplete: stopped before anything ran."
        return f"Incomplete: stopped after {times_ran} of {times_requested} times. {tally}"
    advice = ""
    if verdicts["inconclusive"]:
        advice += (" A bigger batch would settle "
                   f"{'it' if verdicts['inconclusive'] == 1 else 'them'}.")
    if verdicts["none"]:
        advice += (" The check without an answer says why." if verdicts["none"] == 1
                   else " Each check without an answer says why.")
    return f"Inconclusive: {tally[0].lower()}{tally[1:]}{advice}"


def _tally(verdicts: dict[str, int], applicable: int) -> str:
    """What the checks came to, as a sentence: "Of the 4 checks, 2 met the goal
    and 2 can't be told yet." / "The check can't be told yet." """
    words = (("pass", "met the goal"), ("fail", "fell short"),
             ("inconclusive", "can't be told yet"), ("none", "got no answer"))
    if applicable == 1:
        said = next((phrase for key, phrase in words if verdicts[key]), "got no answer")
        return f"The check {said}."
    parts = [f"{verdicts[key]} {phrase}" for key, phrase in words if verdicts[key]]
    joined = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return f"Of the {applicable} checks, {joined}."
