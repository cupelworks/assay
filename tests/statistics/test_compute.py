# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The batch result's arithmetic on plain data: the fold of runs into a check's
sample, the verdicts, the roll-up to a batch status and its sentence."""
import uuid
from datetime import datetime

import pytest

from assay.models import BatchStatus, Comparison, TestStatus, TestTypesModel
from assay.schemas import TestTypeAssignment
from assay.schemas.statistics import StatisticalEngine
from assay.services.statistics import compute
from assay.services.statistics.compute import BatchEntry, BatchRun

GATE = StatisticalEngine.binomial_gate
T_TEST = StatisticalEngine.one_sample_t
GATE_PARAMETERS = {"target": 0.9, "confidence": 0.95}
T_PARAMETERS = {"confidence": 0.95, "difference": 0.05, "spread": 0.1}

CONTAINS = TestTypesModel(name="Contains", engine="contains", comparison=None,
                          config_fields=[])
ROUGE = TestTypesModel(name="ROUGE", engine="rouge", comparison=Comparison.gte,
                       config_fields=[{"key": "threshold", "min": 0.0, "max": 1.0}])
TYPES = {"Contains": CONTAINS, "ROUGE": ROUGE}


def _run(index, status=TestStatus.green, results=None, error=None) -> BatchRun:
    return BatchRun(id=uuid.uuid4(), index=index, execution_id=None, status=status,
                    results=results, error=error)


def _passed(label, passed=True, score=None) -> dict:
    return {label: {"passed": passed, "score": score, "detail": None, "errored": False}}


def _check(label="Contains", runs=(), name=GATE, parameters=GATE_PARAMETERS, floor=29,
           stopped=False, config=None):
    type_name = "ROUGE" if label == "ROUGE" else "Contains"
    assignment = TestTypeAssignment(name=type_name, label=label, config=config)
    return compute.check_result(name, parameters, floor, stopped, assignment,
                                TYPES[type_name], list(runs))


# --- the fold ---


def test_errored_and_not_ran_runs_are_counted_apart_from_the_sample():
    runs = [_run(1, results=_passed("Contains")),
            _run(2, results={"Contains": {"passed": False, "score": None,
                                          "detail": "Judge API timed out", "errored": True}}),
            _run(3, status=TestStatus.not_ran, error="No application URL is set"),
            _run(4, status=TestStatus.red, results=_passed("Contains", passed=False))]

    check = _check(runs=runs)

    assert check.counts.model_dump() == {"evaluated": 2, "passed": 1, "failed": 1,
                                         "errored": 1, "not_ran": 1}
    assert [(p.index, p.passed, p.error) for p in check.series] == [
        (1, True, None), (2, None, "Judge API timed out"),
        (3, None, "No application URL is set"), (4, False, None)]
    assert check.statistic.n == 2


def test_a_check_missing_from_a_runs_results_is_an_error_not_a_failure():
    check = _check(runs=[_run(1, results=_passed("Other"))])

    assert check.series[0].error == compute.NO_RESULT
    assert check.counts.errored == 1


def test_labels_keep_two_checks_of_one_type_apart():
    results = {**_passed("Contains"), **_passed("Contains 2", passed=False)}
    runs = [_run(1, results=results)]

    first, second = _check("Contains", runs), _check("Contains 2", runs)

    assert (first.counts.passed, second.counts.passed) == (1, 0)


# --- the binomial gate ---


def test_a_perfect_record_at_the_floor_passes_with_the_exact_bound():
    check = _check(runs=[_run(i, results=_passed("Contains")) for i in range(1, 30)])

    statistic = check.statistic
    assert statistic.verdict == "pass"
    assert statistic.reason == "Passed 29 of 29 runs: it passes at least 9 times in 10 (95% sure)."
    assert statistic.interval.model_dump() == {"lower": 0.9019, "point": 1.0, "upper": 1.0,
                                               "method": "exact", "level": 0.95,
                                               "sides": "one"}
    assert check.pass_rate == statistic.interval
    assert statistic.rule.model_dump() == {"times": 29, "pass_at_least": 29,
                                           "fail_at_most": 22}
    assert check.target == 0.9 and check.scale is None


def test_an_undecided_gate_says_how_big_a_new_batch_would_decide_it():
    runs = [_run(i, results=_passed("Contains", passed=i > 2)) for i in range(1, 30)]

    statistic = _check(runs=runs).statistic

    assert statistic.verdict == "inconclusive"
    assert statistic.times_to_decide == 239
    assert statistic.times_to_decide_message == (
        "A new batch of about 239 times would likely show that it passes at least 9 times "
        "in 10.")


def test_a_check_under_the_target_reads_would_prove_it_below():
    runs = [_run(i, results=_passed("Contains", passed=i % 5 != 0)) for i in range(1, 30)]

    statistic = _check(runs=runs).statistic

    assert statistic.verdict == "inconclusive"
    assert "would likely show that it passes less than 9 times in 10" in (
        statistic.times_to_decide_message)


def test_a_proven_failure():
    runs = [_run(i, results=_passed("Contains", passed=i % 2 == 0)) for i in range(1, 30)]

    statistic = _check(runs=runs).statistic

    assert statistic.verdict == "fail"
    assert statistic.reason == ("Passed only 14 of 29 runs: it passes less than 9 times in 10 "
                                "(95% sure).")
    assert statistic.times_to_decide is None


def test_a_stopped_batch_below_the_floor_gets_no_verdict_but_keeps_its_rate():
    check = _check(runs=[_run(i, results=_passed("Contains")) for i in range(1, 11)],
                   stopped=True)

    assert check.statistic.verdict is None
    assert "fewer than the 29 this test needs" in check.statistic.reason
    assert check.pass_rate.point == 1.0


def test_a_check_that_never_passed_says_so():
    runs = [_run(i, results=_passed("Contains", passed=False)) for i in range(1, 30)]

    statistic = _check(runs=runs).statistic

    assert statistic.reason == "Failed all 29 runs: it passes less than 9 times in 10 (95% sure)."


def test_no_evaluated_run_no_verdict():
    check = _check(runs=[_run(1, status=TestStatus.not_ran, error="x")])

    assert check.statistic.verdict is None
    assert check.pass_rate is None


# --- the t-test ---


SCORES = [0.61, 0.58, 0.66, 0.55, 0.63, 0.6, 0.57, 0.64, 0.59, 0.62]


def _scored(scores, threshold="0.5", **kwargs):
    runs = [_run(i, results=_passed("ROUGE", passed=s >= float(threshold), score=s))
            for i, s in enumerate(scores, start=1)]
    return _check("ROUGE", runs, name=T_TEST, parameters=T_PARAMETERS, floor=10,
                  config={"threshold": threshold}, **kwargs)


def test_a_mean_score_proven_above_the_threshold():
    check = _scored(SCORES)

    assert (check.scale.min, check.scale.max, check.threshold, check.comparison) == (
        0.0, 1.0, 0.5, "gte")
    assert check.scores.model_dump() == {"n": 10, "mean": 0.605, "sd": 0.0337, "min": 0.55,
                                         "max": 0.66, "p10": 0.568, "p25": 0.5825,
                                         "median": 0.605, "p75": 0.6275, "p90": 0.642}
    statistic = check.statistic
    assert statistic.verdict == "pass"
    assert (statistic.interval.lower, statistic.interval.method) == (0.5854, "t")
    assert (statistic.df, statistic.t) == (9, 9.8389)
    assert check.pass_rate.method == "wilson" and check.pass_rate.sides == "two"


def test_an_undecided_mean_says_how_big_a_new_batch_would_decide_it():
    statistic = _scored(SCORES, threshold="0.6").statistic

    assert statistic.verdict == "inconclusive"
    assert statistic.times_to_decide is not None
    assert "the average is above the 0.6 needed" in statistic.times_to_decide_message


def test_identical_scores_are_decided_by_the_threshold_alone():
    statistic = _scored([0.7] * 10).statistic

    assert statistic.verdict == "pass"
    assert statistic.reason.endswith("— every run scored exactly 0.7.")


def test_identical_scores_are_said_once_rounded_the_same_way():
    statistic = _scored([24.7522] * 10, threshold="30").statistic

    assert statistic.reason == ("Average score 24.8 over 10 runs: below the 30 needed (95% "
                                "sure) — every run scored exactly 24.8.")


def test_a_pass_fail_check_under_a_t_test_has_no_statistic():
    check = _check(runs=[_run(1, results=_passed("Contains"))], name=T_TEST,
                   parameters=T_PARAMETERS, floor=10)

    assert (check.applies, check.statistic) == (False, None)
    assert check.reason == "It only passes or fails: an average needs a check that gives a score"
    assert check.pass_rate.point == 1.0


def test_a_threshold_that_isnt_a_number_has_no_verdict():
    statistic = _scored(SCORES, threshold="0.5").statistic
    assert statistic.verdict == "pass"

    check = _check("ROUGE", [_run(1, results=_passed("ROUGE", score=0.6))], name=T_TEST,
                   parameters=T_PARAMETERS, floor=10, config={"threshold": "high"})
    assert check.statistic.verdict is None
    assert "threshold isn't a number" in check.statistic.reason


# --- the roll-up ---


def _batch(passes_per_check, stopped=False, status=TestStatus.green):
    """One entry, one Contains check per item, each passing in the given runs."""
    assignments = [TestTypeAssignment(name="Contains", label=f"Check {i}")
                   for i in range(len(passes_per_check))]
    runs = [
        _run(index, status=status, results=None if status == TestStatus.not_ran else {
            f"Check {i}": {"passed": index <= passes, "score": None, "detail": None}
            for i, passes in enumerate(passes_per_check)})
        for index in range(1, 30)]
    entry = BatchEntry(entry_id=None, test_id=None, test_set_id=None, test_set_name=None,
                       name="t", recorded_answer=True, assignments=assignments, runs=runs)
    result = compute.compute(GATE, GATE_PARAMETERS, 29, stopped, [entry], TYPES,
                             datetime.now().astimezone())
    return result, runs


@pytest.mark.parametrize("passes,status,sentence", [
    ([29, 29], BatchStatus.passed, "Passed: both checks met the goal."),
    ([29, 29, 29], BatchStatus.passed, "Passed: all 3 checks met the goal."),
    ([29], BatchStatus.passed, "Passed: the check met the goal."),
    ([29, 10], BatchStatus.failed, "Failed: 1 of the 2 checks fell short of the goal."),
    ([10], BatchStatus.failed, "Failed: the check fell short of the goal."),
    ([29, 27], BatchStatus.inconclusive,
     "Inconclusive: of the 2 checks, 1 met the goal and 1 can't be told yet. A bigger batch "
     "would settle it."),
    ([27], BatchStatus.inconclusive,
     "Inconclusive: the check can't be told yet. A bigger batch would settle it."),
])
def test_the_roll_up_and_its_sentence(passes, status, sentence):
    result, runs = _batch(passes)

    rolled = compute.roll_up(result, False, runs)

    assert rolled == status
    assert compute.summary(rolled, result, 29, 29, runs) == sentence


def test_a_stopped_batch_is_incomplete_whatever_its_verdicts():
    result, runs = _batch([29], stopped=True)

    assert compute.roll_up(result, True, runs) == BatchStatus.incomplete


def test_nothing_evaluated_is_not_ran_with_the_first_reason():
    runs = [_run(i, status=TestStatus.not_ran, error="No application URL is set")
            for i in range(1, 30)]
    entry = BatchEntry(entry_id=None, test_id=None, test_set_id=None, test_set_name=None,
                       name="t", recorded_answer=False,
                       assignments=[TestTypeAssignment(name="Contains", label="Contains")],
                       runs=runs)
    result = compute.compute(GATE, GATE_PARAMETERS, 29, False, [entry], TYPES,
                             datetime.now().astimezone())

    status = compute.roll_up(result, False, runs)

    assert status == BatchStatus.not_ran
    assert compute.summary(status, result, 29, 29, runs) == (
        "Not Ran: no run could be carried out — No application URL is set")
    assert result.verdicts == {"pass": 0, "fail": 0, "inconclusive": 0, "none": 1}


# --- failures by entry ---


def _entry_runs(name, outcomes):
    """outcomes: True pass, False fail, None Not Ran, "e" every check errored."""
    runs = []
    for index, outcome in enumerate(outcomes, start=1):
        if outcome is None:
            runs.append(_run(index, status=TestStatus.not_ran, error="x"))
        elif outcome == "e":
            runs.append(_run(index, status=TestStatus.red, results={"Contains": {
                "passed": False, "score": None, "detail": "timeout", "errored": True}}))
        else:
            runs.append(_run(index, results=_passed("Contains", passed=outcome)))
    return BatchEntry(entry_id=uuid.uuid4(), test_id=None, test_set_id=None,
                      test_set_name="s", name=name, recorded_answer=False,
                      assignments=[TestTypeAssignment(name="Contains", label="Contains")],
                      runs=runs)


def test_failures_concentrated_in_one_entry_are_located():
    entries = [_entry_runs("steady", [True] * 29), _entry_runs("flaky", [True] * 15 + [False] * 14),
               _entry_runs("fine", [True] * 28 + [False])]

    diagnostic = compute.failures_by_entry(entries, 0.95)

    assert diagnostic.verdict == "concentrated"
    assert [e.name for e in diagnostic.entries] == ["flaky", "fine", "steady"]
    assert diagnostic.reason == "Most failures come from a few entries: flaky, fine."
    assert diagnostic.df == 2


def test_evenly_spread_failures_are_no_evidence():
    entries = [_entry_runs(n, [True] * 26 + [False] * 3) for n in ("a", "b", "c")]

    diagnostic = compute.failures_by_entry(entries, 0.95)

    assert diagnostic.verdict == "no_evidence"
    assert diagnostic.p_value == 1.0
    assert diagnostic.approximate is True


def test_errored_and_not_ran_runs_are_left_out_and_nothing_failing_has_no_verdict():
    entries = [_entry_runs("a", [True, None, "e"]), _entry_runs("b", [True, True])]

    diagnostic = compute.failures_by_entry(entries, 0.95)

    assert diagnostic.verdict is None
    assert diagnostic.reason == "No run failed: there are no failures to look into."
    assert [(e.passed, e.failed) for e in diagnostic.entries] == [(1, 0), (2, 0)]


def test_a_single_entry_has_no_diagnostic():
    assert compute.failures_by_entry([_entry_runs("a", [False])], 0.95) is None


# --- review fixes: floors, "times to decide", nothing decidable ---


def test_a_t_test_below_its_floor_has_no_verdict_even_when_not_stopped():
    statistic = _scored(SCORES[:3]).statistic

    assert statistic.verdict is None
    assert statistic.reason.startswith("Only 3 scores came back, fewer than the 10 needed")


def test_times_to_decide_is_never_below_the_floor():
    # 4 of 6 against 90% is inconclusive; a new batch of about 9 would likely
    # prove it below, but one under the floor of 29 would be refused
    runs = [_run(i, results=_passed("Contains", passed=i <= 4)) for i in range(1, 7)]

    statistic = _check(runs=runs).statistic

    assert statistic.verdict == "inconclusive"
    assert statistic.times_to_decide == 29
    assert statistic.times_to_decide_message == (
        "A new batch of about 29 times would likely show that it passes less than 9 times "
        "in 10.")


def test_a_check_too_close_to_its_target_says_no_batch_can_decide_it():
    # 26 of 29 against 90%: it would take tens of thousands of times
    runs = [_run(i, results=_passed("Contains", passed=i <= 26)) for i in range(1, 30)]

    statistic = _check(runs=runs).statistic

    assert statistic.verdict == "inconclusive"
    assert statistic.times_to_decide is None
    assert statistic.times_to_decide_message == (
        "Showing that it passes less than 9 times in 10 would take more than 1000 times, more "
        "than one batch can run.")


def test_every_check_errored_in_every_run_is_not_ran_not_inconclusive():
    errored = {"Check 0": {"passed": False, "score": None, "detail": "No judge configured",
                           "errored": True}}
    runs = [_run(i, status=TestStatus.red, results=errored) for i in range(1, 30)]
    entry = BatchEntry(entry_id=None, test_id=None, test_set_id=None, test_set_name=None,
                       name="t", recorded_answer=True,
                       assignments=[TestTypeAssignment(name="Contains", label="Check 0")],
                       runs=runs)
    result = compute.compute(GATE, GATE_PARAMETERS, 29, False, [entry], TYPES,
                             datetime.now().astimezone())

    status = compute.roll_up(result, False, runs)

    assert status == BatchStatus.not_ran
    assert compute.summary(status, result, 29, 29, runs) == (
        "Not Ran: no check gave a result — each one errored in every run (No judge "
        "configured). Fix that first: running more times won't help.")
