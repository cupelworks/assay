"""Two finished batches compared check by check (docs/statistics/dev_notes.md
notes 18 and 22). Pure, like compute.py: the caller loads both batches'
entries and runs.

Entries are matched by entry id (a standalone test's single entry matches
itself), checks by label. Whatever only one batch has is listed as unmatched,
never silently dropped. A is the baseline: every difference is B − A.
"""
from assay import stats_math
from assay.schemas.statistics import (
    CheckComparison,
    ComparisonResult,
    ComparisonSide,
    ComparisonVerdictName,
    EntryComparison,
    IntervalMethod,
    IntervalSides,
    Unmatched,
    UnmatchedSide,
)
from assay.services.statistics.catalogue import MAX_TIMES, POWER, percent
from assay.services.statistics.compute import BatchEntry, fold, make_interval, r4


def _side(entry: BatchEntry, label: str, confidence: float) -> ComparisonSide:
    series, counts = fold(entry.runs, label)
    pass_rate = None
    if counts.evaluated:
        lower, upper = stats_math.wilson_interval(counts.passed, counts.evaluated, confidence)
        pass_rate = make_interval(lower, counts.passed / counts.evaluated, upper,
                                  IntervalMethod.wilson, confidence, IntervalSides.two)
    return ComparisonSide(counts=counts, pass_rate=pass_rate, series=series)


def _points(value: float) -> str:
    """A difference of rates in percentage points: 0.123 → "+12.3 points"."""
    return f"{value * 100:+.1f} points"


def check_comparison(label: str, test_type: str, entry_a: BatchEntry, entry_b: BatchEntry,
                     confidence: float) -> CheckComparison:
    a, b = _side(entry_a, label, confidence), _side(entry_b, label, confidence)
    common = {"label": label, "test_type": test_type, "a": a, "b": b}
    missing = [side for side, data in (("A", a), ("B", b)) if not data.counts.evaluated]
    if missing:
        which = " and ".join(missing)
        return CheckComparison(
            **common, difference=None, verdict=None,
            reason=f"Batch {which} has no evaluated run of this check: nothing to compare.",
            p_value=None, p_value_method=None, times_to_decide=None,
            times_to_decide_message=None)

    test = stats_math.compare_proportions(a.counts.passed, a.counts.evaluated,
                                          b.counts.passed, b.counts.evaluated, confidence)
    rate_a, rate_b = test.passes_a / test.n_a, test.passes_b / test.n_b
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
        if times_to_decide is None:
            message = ("Both passed equally often: no batch size would show a difference "
                       "that isn't there.")
        else:
            message = (f"Two new batches of about {times_to_decide} times each would likely "
                       f"tell {percent(r4(rate_a))} from {percent(r4(rate_b))}")
            if times_to_decide > MAX_TIMES:
                message += f", more than one batch can run ({MAX_TIMES})"
            message += "."
    return CheckComparison(
        **common,
        difference=make_interval(test.lower, test.difference, test.upper,
                                 IntervalMethod.newcombe, confidence, IntervalSides.two),
        verdict=ComparisonVerdictName(test.verdict.value), reason=reason,
        p_value=r4(test.p_value), p_value_method=test.p_value_method,
        times_to_decide=times_to_decide, times_to_decide_message=message,
    )


def compare(entries_a: list[BatchEntry], entries_b: list[BatchEntry],
            confidence: float) -> ComparisonResult:
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
            checks.append(check_comparison(assignment.label, assignment.name, entry_a,
                                           entry_b, confidence))
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

    verdicts = {"better": 0, "worse": 0, "no_difference": 0, "none": 0}
    for entry in compared:
        for check in entry.checks:
            verdicts[check.verdict.value if check.verdict else "none"] += 1
    return ComparisonResult(verdicts=verdicts, summary=summary(verdicts, unmatched),
                            entries=compared, unmatched=unmatched)


def summary(verdicts: dict[str, int], unmatched: list[Unmatched]) -> str:
    total = sum(verdicts.values())
    checks = f"{total} {'check' if total == 1 else 'checks'}"
    if total == 0:
        sentence = "Nothing to compare: the two batches share no check."
    elif verdicts["worse"]:
        sentence = (f"B is worse on {verdicts['worse']} of {checks}"
                    + (f" and better on {verdicts['better']}" if verdicts["better"] else "")
                    + ".")
    elif verdicts["better"]:
        sentence = f"B is better on {verdicts['better']} of {checks}, worse on none."
    elif verdicts["no_difference"]:
        sentence = f"No real difference on any of the {checks} at this size."
    else:
        sentence = "No check could be compared: one batch evaluated none of them."
    if unmatched:
        what = "entry or check is" if len(unmatched) == 1 else "entries or checks are"
        sentence += f" {len(unmatched)} {what} in one batch only."
    return sentence
