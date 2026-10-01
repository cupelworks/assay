"""Two finished batches compared check by check (docs/statistics/dev_notes.md
notes 18, 22 and 24). Pure, like compute.py: the caller loads both batches'
entries and runs, and the catalogue rows of their checks.

Entries are matched by entry id (a standalone test's single entry matches
itself), checks by label. Whatever only one batch has is listed as unmatched,
never silently dropped. A is the baseline: every difference is B − A.

The comparison tests:
- `pass_rates` — Newcombe's interval of B − A decides better / worse / no
  real difference; chi-square's or Fisher's p-value beside it.
- `no_worse` — non-inferiority: Newcombe's one-sided bounds against −margin.
- `mean_scores` — Welch's t-test of the mean scores (scored checks only).
- `score_ranks` — Mann–Whitney on the scores (scored checks only).
- `paired_entries` — one verdict over every (entry, check) pair: the paired
  t-test of the pass-rate differences, Wilcoxon's p-value beside it.
"""
from assay import stats_math
from assay.models import TestTypesModel
from assay.schemas.statistics import (
    CheckComparison,
    ComparisonResult,
    ComparisonSide,
    ComparisonVerdictName,
    EntryComparison,
    IntervalMethod,
    IntervalSides,
    Pair,
    PairedComparison,
    ScoreSummaryOut,
    StatisticalTestName,
    Unmatched,
    UnmatchedSide,
)
from assay.services.statistics.catalogue import MAX_TIMES, POWER, percent
from assay.services.statistics.compute import BatchEntry, fold, make_interval, r4

# the fewest scores per batch Mann–Whitney can conclude with at 95%, and the
# fewest pairs a paired comparison can (note 5)
MANN_WHITNEY_FLOOR = 4
PAIRED_FLOOR = 6

_BETTER, _WORSE = ComparisonVerdictName.better, ComparisonVerdictName.worse
_SAME = ComparisonVerdictName.no_difference


def _summary(scores: list[float]) -> ScoreSummaryOut | None:
    if not scores:
        return None
    s = stats_math.summarize_scores(scores)
    return ScoreSummaryOut(n=s.n, mean=r4(s.mean), sd=r4(s.sd), min=r4(s.minimum),
                           max=r4(s.maximum), p10=r4(s.p10), p25=r4(s.p25),
                           median=r4(s.median), p75=r4(s.p75), p90=r4(s.p90))


def _side(entry: BatchEntry, label: str, confidence: float,
          scored: bool) -> tuple[ComparisonSide, list[float]]:
    series, counts = fold(entry.runs, label)
    pass_rate = None
    if counts.evaluated:
        lower, upper = stats_math.wilson_interval(counts.passed, counts.evaluated, confidence)
        pass_rate = make_interval(lower, counts.passed / counts.evaluated, upper,
                                  IntervalMethod.wilson, confidence, IntervalSides.two)
    scores = [p.score for p in series if p.passed is not None and p.score is not None]
    side = ComparisonSide(counts=counts, pass_rate=pass_rate,
                          scores=_summary(scores) if scored else None, series=series)
    return side, scores


def _points(value: float) -> str:
    """A difference of rates in percentage points: 0.123 → "+12.3 points"."""
    return f"{value * 100:+.1f} points"


def _rate(side: ComparisonSide) -> float:
    return side.counts.passed / side.counts.evaluated


def _decide_message(times: int | None, what: str) -> str | None:
    if times is None:
        return None
    message = f"Two new batches of about {times} times each would likely {what}"
    if times > MAX_TIMES:
        message += f", more than one batch can run ({MAX_TIMES})"
    return message + "."


def _no_verdict(common: dict, reason: str) -> CheckComparison:
    return CheckComparison(**common, difference=None, verdict=None, reason=reason,
                           p_value=None, p_value_method=None, times_to_decide=None,
                           times_to_decide_message=None)


# ── one check, by test ───────────────────────────────────────────────────────


def _pass_rates(common: dict, a: ComparisonSide, b: ComparisonSide,
                confidence: float) -> CheckComparison:
    test = stats_math.compare_proportions(a.counts.passed, a.counts.evaluated,
                                          b.counts.passed, b.counts.evaluated, confidence)
    rate_a, rate_b = _rate(a), _rate(b)
    sure = percent(confidence)
    between = f"between {_points(test.lower)} and {_points(test.upper)}"
    times_to_decide = message = None
    if test.verdict == stats_math.ComparisonVerdict.better:
        reason = (f"B passes more often than A: {percent(r4(rate_b))} against "
                  f"{percent(r4(rate_a))}, {sure} confident the difference is {between}.")
    elif test.verdict == stats_math.ComparisonVerdict.worse:
        reason = (f"B passes less often than A: {percent(r4(rate_b))} against "
                  f"{percent(r4(rate_a))}, {sure} confident the difference is {between}.")
    else:
        reason = (f"No real difference at this size: {percent(r4(rate_a))} for A, "
                  f"{percent(r4(rate_b))} for B; the difference could be anywhere "
                  f"{between}.")
        times_to_decide = stats_math.runs_needed_for_proportions(rate_a, rate_b, confidence,
                                                                 POWER)
        message = _decide_message(
            times_to_decide, f"tell {percent(r4(rate_a))} from {percent(r4(rate_b))}")
        if times_to_decide is None:
            message = ("Both passed equally often: no batch size would show a difference "
                       "that isn't there.")
    return CheckComparison(
        **common,
        difference=make_interval(test.lower, test.difference, test.upper,
                                 IntervalMethod.newcombe, confidence, IntervalSides.two),
        verdict=ComparisonVerdictName(test.verdict.value), reason=reason,
        p_value=r4(test.p_value), p_value_method=test.p_value_method,
        times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def _no_worse(common: dict, a: ComparisonSide, b: ComparisonSide, confidence: float,
              margin: float) -> CheckComparison:
    test = stats_math.non_inferiority(a.counts.passed, a.counts.evaluated, b.counts.passed,
                                      b.counts.evaluated, margin, confidence)
    rate_a, rate_b = _rate(a), _rate(b)
    sure, allowed = percent(confidence), _points(-margin)
    rates = f"{percent(r4(rate_b))} against {percent(r4(rate_a))}"
    times_to_decide = message = None
    if test.verdict == "no_worse":
        reason = (f"{sure} confident B is no worse than A by more than "
                  f"{margin * 100:g} points ({rates}; B − A is at least "
                  f"{_points(test.lower)}).")
    elif test.verdict == "worse":
        reason = (f"{sure} confident B is worse than A by more than {margin * 100:g} points "
                  f"({rates}; B − A is at most {_points(test.upper)}).")
    else:
        reason = (f"Not proven either way: {rates}; B − A could be as low as "
                  f"{_points(test.lower)}, below the allowed {allowed}.")
        times_to_decide = stats_math.runs_needed_for_non_inferiority(
            rate_a, rate_b, margin, confidence, POWER)
        message = _decide_message(times_to_decide,
                                  f"show B no worse by more than {margin * 100:g} points")
        if times_to_decide is None:
            message = ("B's observed rate is already below what the margin allows: no batch "
                       "size would likely show it no worse.")
    return CheckComparison(
        **common,
        difference=make_interval(test.lower, test.difference, test.upper,
                                 IntervalMethod.newcombe, confidence, IntervalSides.one),
        verdict=ComparisonVerdictName(test.verdict), reason=reason, p_value=None,
        p_value_method=None, times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def _direction(difference_sign: int, higher_is_better: bool) -> ComparisonVerdictName:
    """B's change in score as a verdict: higher is better for every metric
    today (`gte`); a lower-is-better type (`lte`) flips it."""
    up = difference_sign > 0
    return _BETTER if up == higher_is_better else _WORSE


def _mean_scores(common: dict, scores_a: list[float], scores_b: list[float],
                 confidence: float, higher_is_better: bool) -> CheckComparison:
    if len(scores_a) < 2 or len(scores_b) < 2:
        return _no_verdict(common, "Welch's t-test needs at least two scores in each batch.")
    test = stats_math.welch(scores_a, scores_b, confidence)
    sure = percent(confidence)
    means = f"mean {test.mean_b:.4g} for B, {test.mean_a:.4g} for A"
    times_to_decide = message = None
    if test.lower > 0 or test.upper < 0:
        verdict = _direction(1 if test.lower > 0 else -1, higher_is_better)
        word = "higher" if test.lower > 0 else "lower"
        reason = (f"B scores {word} than A ({means}), {sure} confident the difference is "
                  f"between {test.lower:+.4g} and {test.upper:+.4g}.")
        if not higher_is_better:
            reason += " Lower is better for this check."
    else:
        verdict = _SAME
        reason = (f"No real difference at this size ({means}); the difference could be "
                  f"anywhere between {test.lower:+.4g} and {test.upper:+.4g}.")
        spread = max(stats_math.summarize_scores(scores_a).sd or 0.0,
                     stats_math.summarize_scores(scores_b).sd or 0.0)
        times_to_decide = stats_math.runs_needed_for_means(spread, test.difference,
                                                           confidence, POWER)
        message = _decide_message(times_to_decide,
                                  f"tell a difference of {abs(test.difference):.4g}")
        if times_to_decide is None:
            message = ("The means are equal: no batch size would show a difference that "
                       "isn't there.")
    return CheckComparison(
        **common,
        difference=make_interval(test.lower, test.difference, test.upper, IntervalMethod.t,
                                 confidence, IntervalSides.two),
        verdict=verdict, reason=reason, p_value=r4(test.p_value), p_value_method="welch",
        times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def _score_ranks(common: dict, scores_a: list[float], scores_b: list[float],
                 confidence: float, higher_is_better: bool) -> CheckComparison:
    if min(len(scores_a), len(scores_b)) < MANN_WHITNEY_FLOOR:
        return _no_verdict(common, f"Mann–Whitney needs at least {MANN_WHITNEY_FLOOR} scores "
                                   "in each batch to conclude anything.")
    test = stats_math.mann_whitney(scores_a, scores_b)
    beats = f"a score of B beats one of A {percent(r4(test.effect))} of the time"
    if test.p_value <= 1 - confidence and test.effect != 0.5:
        verdict = _direction(1 if test.effect > 0.5 else -1, higher_is_better)
        word = "higher" if test.effect > 0.5 else "lower"
        reason = f"B's scores are {word} than A's: {beats} (p = {test.p_value:.4g})."
    else:
        verdict = _SAME
        reason = f"No real difference at this size: {beats} (p = {test.p_value:.4g})."
    return CheckComparison(
        **common, difference=None, effect=r4(test.effect), verdict=verdict, reason=reason,
        p_value=r4(test.p_value), p_value_method=f"mann_whitney_{test.method}",
        times_to_decide=None, times_to_decide_message=None,
    )


def compare_check(name: StatisticalTestName, parameters: dict[str, float], label: str,
                  test_type: str, row: TestTypesModel | None, entry_a: BatchEntry,
                  entry_b: BatchEntry) -> CheckComparison:
    confidence = parameters["confidence"]
    scored = row is not None and row.comparison is not None
    a, scores_a = _side(entry_a, label, confidence, scored)
    b, scores_b = _side(entry_b, label, confidence, scored)
    common = {"label": label, "test_type": test_type, "a": a, "b": b}
    missing = [side for side, data in (("A", a), ("B", b)) if not data.counts.evaluated]
    if missing:
        return _no_verdict(common, f"Batch {' and '.join(missing)} has no evaluated run of "
                                   "this check: nothing to compare.")
    if name in (StatisticalTestName.mean_scores, StatisticalTestName.score_ranks):
        if not scored:
            return _no_verdict(common, "Pass/fail only: comparing scores needs a check "
                                       "scored on a scale.")
        higher_is_better = row.comparison.value == "gte"
        compare_scores = (_mean_scores if name == StatisticalTestName.mean_scores
                          else _score_ranks)
        return compare_scores(common, scores_a, scores_b, confidence, higher_is_better)
    if name == StatisticalTestName.no_worse:
        return _no_worse(common, a, b, confidence, parameters["margin"])
    checked = _pass_rates(common, a, b, confidence)
    if name == StatisticalTestName.paired_entries:
        checked.verdict = None
        checked.reason = "Judged together with every other pair: see `paired`."
        checked.times_to_decide = checked.times_to_decide_message = None
    return checked


def check_comparison(label: str, test_type: str, entry_a: BatchEntry, entry_b: BatchEntry,
                     confidence: float) -> CheckComparison:
    """Pass rates, B against A, for one check."""
    return compare_check(StatisticalTestName.pass_rates, {"confidence": confidence}, label,
                         test_type, None, entry_a, entry_b)


# ── every pair at once ───────────────────────────────────────────────────────


def paired(entries: list[EntryComparison], confidence: float) -> PairedComparison:
    pairs = [
        Pair(entry_id=entry.entry_id, name=entry.name, label=check.label,
             rate_a=check.a.pass_rate.point, rate_b=check.b.pass_rate.point,
             difference=r4(check.b.pass_rate.point - check.a.pass_rate.point))
        for entry in entries for check in entry.checks
        if check.a.pass_rate is not None and check.b.pass_rate is not None
    ]
    common = {"n_pairs": len(pairs), "pairs": pairs}
    if len(pairs) < PAIRED_FLOOR:
        return PairedComparison(
            **common, difference=None, verdict=None, p_value=None, p_value_wilcoxon=None,
            wilcoxon_method=None,
            reason=f"{len(pairs)} {'pair' if len(pairs) == 1 else 'pairs'} both batches "
                   f"evaluated: at least {PAIRED_FLOOR} are needed to compare entry by entry.")
    differences = [p.rate_b - p.rate_a for p in pairs]
    test = stats_math.paired_t(differences, confidence)
    signed = stats_math.wilcoxon_signed_rank(differences)
    sure = percent(confidence)
    between = f"between {_points(test.lower)} and {_points(test.upper)}"
    if test.lower > 0:
        verdict = _BETTER
        reason = (f"Entry by entry, B passes more often than A: on average "
                  f"{_points(test.difference)}, {sure} confident it's {between}.")
    elif test.upper < 0:
        verdict = _WORSE
        reason = (f"Entry by entry, B passes less often than A: on average "
                  f"{_points(test.difference)}, {sure} confident it's {between}.")
    else:
        verdict = _SAME
        reason = (f"Entry by entry, no real difference at this size: on average "
                  f"{_points(test.difference)}, anywhere {between}.")
    agrees = (signed.p_value <= 1 - confidence) == (verdict != _SAME)
    if not agrees:
        reason += (f" Wilcoxon's test disagrees (p = {signed.p_value:.4g}): the differences "
                   "may not be bell-shaped — read it with care.")
    return PairedComparison(
        **common,
        difference=make_interval(test.lower, test.difference, test.upper, IntervalMethod.t,
                                 confidence, IntervalSides.two),
        verdict=verdict, reason=reason, p_value=r4(test.p_value),
        p_value_wilcoxon=r4(signed.p_value), wilcoxon_method=signed.method,
    )


# ── the batches ──────────────────────────────────────────────────────────────


def compare(entries_a: list[BatchEntry], entries_b: list[BatchEntry],
            parameters: dict[str, float],
            name: StatisticalTestName = StatisticalTestName.pass_rates,
            types: dict[str, TestTypesModel] | None = None) -> ComparisonResult:
    types = types or {}
    by_id_b = {entry.entry_id: entry for entry in entries_b}
    ids_a = {entry.entry_id for entry in entries_a}
    compared, unmatched = [], []
    for entry_a in entries_a:
        entry_b = by_id_b.get(entry_a.entry_id)
        if entry_b is None:
            unmatched.append(Unmatched(entry_id=entry_a.entry_id, name=entry_a.name,
                                       label=None, only_in=UnmatchedSide.a))
            continue
        labels_b = {a.label: a for a in entry_b.assignments}
        checks = []
        for assignment in entry_a.assignments:
            if assignment.label not in labels_b:
                unmatched.append(Unmatched(entry_id=entry_a.entry_id, name=entry_a.name,
                                           label=assignment.label, only_in=UnmatchedSide.a))
                continue
            checks.append(compare_check(name, parameters, assignment.label, assignment.name,
                                        types.get(assignment.name), entry_a, entry_b))
        labels_a = {a.label for a in entry_a.assignments}
        unmatched.extend(
            Unmatched(entry_id=entry_b.entry_id, name=entry_b.name, label=label,
                      only_in=UnmatchedSide.b)
            for label in labels_b if label not in labels_a)
        compared.append(EntryComparison(
            entry_id=entry_a.entry_id, test_id=entry_a.test_id,
            test_set_name=entry_a.test_set_name, name=entry_a.name, checks=checks))
    unmatched.extend(
        Unmatched(entry_id=entry.entry_id, name=entry.name, label=None,
                  only_in=UnmatchedSide.b)
        for entry in entries_b if entry.entry_id not in ids_a)

    verdicts = {verdict.value: 0 for verdict in ComparisonVerdictName} | {"none": 0}
    paired_result = None
    if name == StatisticalTestName.paired_entries:
        paired_result = paired(compared, parameters["confidence"])
        verdicts[paired_result.verdict.value if paired_result.verdict else "none"] += 1
        sentence = paired_result.reason
        if unmatched:
            what = "entry or check is" if len(unmatched) == 1 else "entries or checks are"
            sentence += f" {len(unmatched)} {what} in one batch only."
    else:
        for entry in compared:
            for check in entry.checks:
                verdicts[check.verdict.value if check.verdict else "none"] += 1
        sentence = summary(verdicts, unmatched)
    return ComparisonResult(verdicts=verdicts, summary=sentence, entries=compared,
                            unmatched=unmatched, paired=paired_result)


def summary(verdicts: dict[str, int], unmatched: list[Unmatched]) -> str:
    verdicts = {key: verdicts.get(key, 0) for key in
                ("better", "worse", "no_difference", "no_worse", "inconclusive", "none")}
    total = sum(verdicts.values())
    checks = f"{total} {'check' if total == 1 else 'checks'}"
    if total == 0:
        sentence = "Nothing to compare: the two batches share no check."
    elif verdicts["worse"]:
        others = [f"better on {verdicts['better']}" if verdicts["better"] else "",
                  f"no worse on {verdicts['no_worse']}" if verdicts["no_worse"] else ""]
        extra = " and ".join(o for o in others if o)
        sentence = f"B is worse on {verdicts['worse']} of {checks}" + (
            f" and {extra}" if extra else "") + "."
    elif verdicts["better"]:
        sentence = f"B is better on {verdicts['better']} of {checks}, worse on none."
    elif verdicts["no_worse"] and verdicts["no_worse"] == total:
        sentence = f"B is no worse than A on every one of the {checks}."
    elif verdicts["no_worse"] or verdicts["inconclusive"]:
        sentence = (f"B is no worse on {verdicts['no_worse']} of {checks}; "
                    f"{verdicts['inconclusive']} not proven either way.")
    elif verdicts["no_difference"]:
        sentence = f"No real difference on any of the {checks} at this size."
    else:
        sentence = "No check could be compared: one batch evaluated none of them."
    if unmatched:
        what = "entry or check is" if len(unmatched) == 1 else "entries or checks are"
        sentence += f" {len(unmatched)} {what} in one batch only."
    return sentence
