# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Two finished batches compared check by check. Pure, like compute.py: the
caller loads both batches' entries and runs, and the catalogue rows of their
checks.

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
    ComparisonOutcome,
    ComparisonResult,
    ComparisonSide,
    ComparisonVerdictName,
    EntryComparison,
    IntervalMethod,
    IntervalSides,
    Pair,
    PairedComparison,
    ScoreSummaryOut,
    StatisticalEngine,
    Unmatched,
    UnmatchedSide,
)
from assay.services.statistics.catalogue import MAX_TIMES, POWER, sure
from assay.services.statistics.compute import BatchEntry, fold, make_interval, r4

# the fewest scores per batch Mann–Whitney can conclude with at 95%, and the
# fewest pairs a paired comparison can
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


def _whole(rate: float) -> str:
    """A pass rate as people read it: 0.9655 → "97%"."""
    return f"{rate * 100:.0f}%"


def _points(value: float) -> str:
    """A gap between pass rates in whole points: 0.123 → "12 points"."""
    points = abs(value) * 100
    return f"{points:.0f} {'point' if round(points) == 1 else 'points'}"


def _change(verdict: ComparisonVerdictName) -> str:
    return "improvement" if verdict == ComparisonVerdictName.better else "drop"


def _rate(side: ComparisonSide) -> float:
    return side.counts.passed / side.counts.evaluated


def _decide_message(times: int | None) -> str | None:
    if times is None:
        return None
    if times > MAX_TIMES:
        return (f"Telling them apart would take two new batches of about {times} times each, "
                f"more than one batch can run ({MAX_TIMES}).")
    return f"Two new batches of about {times} times each would likely tell them apart."


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
    rates = f"it passed {_whole(rate_b)} of the time against A's {_whole(rate_a)}"
    times_to_decide = message = None
    if test.verdict == stats_math.ComparisonVerdict.better:
        reason = (f"B is better: {rates}. That's a real improvement, not chance "
                  f"({sure(confidence)}).")
    elif test.verdict == stats_math.ComparisonVerdict.worse:
        reason = f"B is worse: {rates}. That's a real drop, not chance ({sure(confidence)})."
    else:
        if rate_a == rate_b:
            reason = ("No difference: both failed every time." if rate_a == 0 else
                      "No difference: both passed every time." if rate_a == 1 else
                      f"No difference: both passed {_whole(rate_a)} of the time.")
        else:
            reason = (f"No clear difference: {_whole(rate_a)} for A, {_whole(rate_b)} for B. "
                      f"With this many runs, a gap that small could be chance.")
        times_to_decide = stats_math.runs_needed_for_proportions(rate_a, rate_b, confidence,
                                                                 POWER)
        message = _decide_message(times_to_decide)
        if times_to_decide is None:
            both = ("Both failed every time" if rate_a == 0 else
                    "Both passed every time" if rate_a == 1 else "Both passed equally often")
            message = f"{both}: no number of runs would show a difference that isn't there."
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
    allowed = _points(margin)
    rates = f"B passed {_whole(rate_b)} of the time against A's {_whole(rate_a)}"
    times_to_decide = message = None
    if test.verdict == "no_worse":
        reason = (f"B is still as good: at most {allowed} below A ({rates}, "
                  f"{sure(confidence)}).")
    elif test.verdict == "worse":
        reason = f"B is worse: more than {allowed} below A ({rates}, {sure(confidence)})."
    else:
        reason = (f"Can't tell yet: {rates}. With this many runs, B could still be more than "
                  f"{allowed} worse.")
        times_to_decide = stats_math.runs_needed_for_non_inferiority(
            rate_a, rate_b, margin, confidence, POWER)
        if times_to_decide is not None:
            message = (f"Two new batches of about {times_to_decide} times each would likely "
                       f"settle it" + (f", more than one batch can run ({MAX_TIMES})"
                                        if times_to_decide > MAX_TIMES else "") + ".")
        else:
            message = (f"B already passes more than {allowed} less often than A: more runs "
                       "wouldn't show it's still as good.")
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
        return _no_verdict(common, "Comparing averages needs at least two scores in each "
                                   "batch.")
    test = stats_math.welch(scores_a, scores_b, confidence)
    averages = f"an average of {test.mean_b:.3g} against A's {test.mean_a:.3g}"
    times_to_decide = message = None
    if test.lower > 0 or test.upper < 0:
        verdict = _direction(1 if test.lower > 0 else -1, higher_is_better)
        word = "higher" if test.lower > 0 else "lower"
        reason = (f"B scores {word}: {averages}. That's a real {_change(verdict)}, not "
                  f"chance ({sure(confidence)}).")
        if not higher_is_better:
            reason += " Lower is better for this check."
    else:
        verdict = _SAME
        reason = (f"No clear difference: an average of {test.mean_b:.3g} for B, "
                  f"{test.mean_a:.3g} for A. With this many runs, a gap that small could be "
                  f"chance.")
        spread = max(stats_math.summarize_scores(scores_a).sd or 0.0,
                     stats_math.summarize_scores(scores_b).sd or 0.0)
        times_to_decide = stats_math.runs_needed_for_means(spread, test.difference,
                                                           confidence, POWER)
        message = _decide_message(times_to_decide)
        if times_to_decide is None:
            message = ("The averages are equal: no number of runs would show a difference "
                       "that isn't there.")
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
        return _no_verdict(common, f"Comparing scores needs at least {MANN_WHITNEY_FLOOR} "
                                   "scores in each batch.")
    test = stats_math.mann_whitney(scores_a, scores_b)
    beats = (f"picking one run of each, B scores higher {_whole(test.effect)} of the time")
    if test.p_value <= 1 - confidence and test.effect != 0.5:
        verdict = _direction(1 if test.effect > 0.5 else -1, higher_is_better)
        word = "higher" if test.effect > 0.5 else "lower"
        reason = (f"B tends to score {word}: {beats}. That's a real {_change(verdict)}, not "
                  f"chance ({sure(confidence)}).")
        if not higher_is_better:
            reason += " Lower is better for this check."
    else:
        verdict = _SAME
        reason = f"No clear difference: {beats}. With this many runs, that could be chance."
    return CheckComparison(
        **common, difference=None, effect=r4(test.effect), verdict=verdict, reason=reason,
        p_value=r4(test.p_value), p_value_method=f"mann_whitney_{test.method}",
        times_to_decide=None, times_to_decide_message=None,
    )


def compare_check(name: StatisticalEngine, parameters: dict[str, float], label: str,
                  test_type: str, row: TestTypesModel | None, entry_a: BatchEntry,
                  entry_b: BatchEntry) -> CheckComparison:
    confidence = parameters["confidence"]
    scored = row is not None and row.comparison is not None
    a, scores_a = _side(entry_a, label, confidence, scored)
    b, scores_b = _side(entry_b, label, confidence, scored)
    common = {"label": label, "test_type": test_type, "a": a, "b": b}
    missing = [side for side, data in (("A", a), ("B", b)) if not data.counts.evaluated]
    if missing:
        return _no_verdict(common, f"Batch {' and '.join(missing)} has no result for this "
                                   "check: nothing to compare.")
    if name in (StatisticalEngine.mean_scores, StatisticalEngine.score_ranks):
        if not scored:
            return _no_verdict(common, "It only passes or fails: comparing scores needs a "
                                       "check that gives a score.")
        higher_is_better = row.comparison.value == "gte"
        compare_scores = (_mean_scores if name == StatisticalEngine.mean_scores
                          else _score_ranks)
        return compare_scores(common, scores_a, scores_b, confidence, higher_is_better)
    if name == StatisticalEngine.no_worse:
        return _no_worse(common, a, b, confidence, parameters["margin"])
    checked = _pass_rates(common, a, b, confidence)
    if name == StatisticalEngine.paired_entries:
        checked.verdict = None
        checked.reason = "Judged together with the other entries: see the overall answer."
        checked.times_to_decide = checked.times_to_decide_message = None
    return checked


def check_comparison(label: str, test_type: str, entry_a: BatchEntry, entry_b: BatchEntry,
                     confidence: float) -> CheckComparison:
    """Pass rates, B against A, for one check."""
    return compare_check(StatisticalEngine.pass_rates, {"confidence": confidence}, label,
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
            reason=f"Only {len(pairs)} {'check' if len(pairs) == 1 else 'checks'} across the "
                   f"entries ran in both batches; comparing entry by entry needs at least "
                   f"{PAIRED_FLOOR}.")
    differences = [p.rate_b - p.rate_a for p in pairs]
    test = stats_math.paired_t(differences, confidence)
    signed = stats_math.wilcoxon_signed_rank(differences)
    gap = _points(test.difference)
    if test.lower > 0:
        verdict = _BETTER
        reason = (f"Entry by entry, B is better: on average it passes {gap} more often than "
                  f"A. That's a real improvement, not chance ({sure(confidence)}).")
    elif test.upper < 0:
        verdict = _WORSE
        reason = (f"Entry by entry, B is worse: on average it passes {gap} less often than "
                  f"A. That's a real drop, not chance ({sure(confidence)}).")
    else:
        verdict = _SAME
        if round(abs(test.difference) * 100) == 0:
            reason = "Entry by entry, no clear difference: on average B passes as often as A."
        else:
            more = "more" if test.difference > 0 else "less"
            reason = (f"Entry by entry, no clear difference: on average B passes {gap} "
                      f"{more} often than A, which could be chance.")
    agrees = (signed.p_value <= 1 - confidence) == (verdict != _SAME)
    if not agrees:
        reason += " A second way of checking disagrees, so take it with care."
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
            name: StatisticalEngine = StatisticalEngine.pass_rates,
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
    if name == StatisticalEngine.paired_entries:
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


VERDICT_KEYS = ("better", "worse", "no_difference", "no_worse", "inconclusive", "none")


def all_verdicts(verdicts: dict[str, int]) -> dict[str, int]:
    """The counts with every key, zero where a verdict didn't occur."""
    return {key: verdicts.get(key, 0) for key in VERDICT_KEYS}


def outcome(verdicts: dict[str, int]) -> ComparisonOutcome:
    """The comparison's one overall answer, in the order `summary` leads with."""
    verdicts = all_verdicts(verdicts)
    total = sum(verdicts.values())
    if verdicts["worse"]:
        return ComparisonOutcome.worse
    if verdicts["better"]:
        return ComparisonOutcome.better
    if total and verdicts["no_worse"] == total:
        return ComparisonOutcome.no_worse
    if verdicts["no_worse"] or verdicts["inconclusive"]:
        return ComparisonOutcome.inconclusive
    if verdicts["no_difference"]:
        return ComparisonOutcome.no_difference
    return ComparisonOutcome.none


def summary(verdicts: dict[str, int], unmatched: list[Unmatched]) -> str:
    verdicts = all_verdicts(verdicts)
    total = sum(verdicts.values())
    checks = f"{total} {'check' if total == 1 else 'checks'}"
    if total == 0:
        sentence = "Nothing to compare: the two batches share no check."
    elif total == 1:
        sentence = {
            "better": "B is better on the check.", "worse": "B is worse on the check.",
            "no_worse": "B is still as good as A on the check.",
            "inconclusive": "Can't tell yet whether B is still as good on the check.",
            "no_difference": "No clear difference on the check.",
            "none": "The check couldn't be compared: one batch has no result for it.",
        }[next(key for key, count in verdicts.items() if count)]
    elif verdicts["worse"]:
        others = [f"better on {verdicts['better']}" if verdicts["better"] else "",
                  f"still as good on {verdicts['no_worse']}" if verdicts["no_worse"] else ""]
        extra = " and ".join(o for o in others if o)
        sentence = f"B is worse on {verdicts['worse']} of {checks}" + (
            f" and {extra}" if extra else "") + "."
    elif verdicts["better"]:
        sentence = f"B is better on {verdicts['better']} of {checks}, worse on none."
    elif verdicts["no_worse"] and verdicts["no_worse"] == total:
        sentence = f"B is still as good as A on all {checks}."
    elif verdicts["no_worse"] or verdicts["inconclusive"]:
        sentence = (f"B is still as good on {verdicts['no_worse']} of {checks}; "
                    f"{verdicts['inconclusive']} can't be told yet.")
    elif verdicts["no_difference"]:
        sentence = f"No clear difference on any of the {checks}."
    else:
        sentence = "No check could be compared: one batch has no result for any of them."
    if unmatched:
        what = "entry or check is" if len(unmatched) == 1 else "entries or checks are"
        sentence += f" {len(unmatched)} {what} in one batch only."
    return sentence
