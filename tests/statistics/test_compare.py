"""Two batches compared on plain data: the matching, the verdicts against
published 2×2 examples, and what can't be compared."""
import uuid

from assay.models import TestStatus
from assay.schemas import TestTypeAssignment
from assay.services.statistics import compare
from assay.services.statistics.compute import BatchEntry, BatchRun


def _entry(passes: int, n: int = 29, entry_id=None, labels=("Check",), name="t",
           errored: int = 0) -> BatchEntry:
    runs = []
    for index in range(1, n + 1):
        results = {label: {"passed": index <= passes, "score": None, "detail": None,
                           "errored": index > n - errored} for label in labels}
        runs.append(BatchRun(id=uuid.uuid4(), index=index, execution_id=None,
                             status=TestStatus.green, results=results, error=None))
    return BatchEntry(entry_id=entry_id, test_id=None, test_set_id=None, test_set_name=None,
                      name=name, recorded_answer=False,
                      assignments=[TestTypeAssignment(name="Contains", label=label)
                                   for label in labels],
                      runs=runs)


def _check(a: BatchEntry, b: BatchEntry):
    return compare.check_comparison("Check", "Contains", a, b, 0.95)


def test_newcombes_published_example_is_better():
    # Newcombe (1998), example (a): 56/70 against 48/80, method 10: 0.0524 to 0.3339
    check = _check(_entry(48, 80), _entry(56, 70))

    assert check.verdict == "better"
    assert (check.difference.lower, check.difference.point, check.difference.upper) == (
        0.0524, 0.2, 0.3339)
    assert (check.difference.method, check.difference.sides) == ("newcombe", "two")
    assert check.p_value_method == "chi_square"


def test_newcombes_small_example_is_worse_with_fishers_p_value():
    # example (b): 9/10 against 3/10 → B − A = −0.6, interval −0.8090 to −0.1705
    check = _check(_entry(9, 10), _entry(3, 10))

    assert check.verdict == "worse"
    assert (check.difference.lower, check.difference.upper) == (-0.809, -0.1705)
    assert check.p_value_method == "fisher_exact"
    assert check.reason == ("B is worse: it passed 30% of the time against A's 90%. That's a "
                            "real drop, not chance (95% sure).")


def test_no_real_difference_says_how_big_two_new_batches_would_need_to_be():
    check = _check(_entry(27), _entry(28))

    assert check.verdict == "no_difference"
    assert check.times_to_decide == 647
    assert check.times_to_decide_message == (
        "Two new batches of about 647 times each would likely tell them apart.")
    assert check.reason == ("No clear difference: 93% for A, 97% for B. With this many runs, "
                            "a gap that small could be chance.")


def test_equal_rates_can_never_be_told_apart():
    check = _check(_entry(29), _entry(29))

    assert check.verdict == "no_difference"
    assert check.times_to_decide is None
    assert "no number of runs would show a difference" in check.times_to_decide_message
    assert check.reason == "No difference: both passed every time."


def test_errored_runs_are_left_out_of_both_samples():
    check = _check(_entry(20, errored=5), _entry(29))

    assert check.a.counts.evaluated == 24 and check.a.counts.errored == 5


def test_a_side_with_nothing_evaluated_has_no_verdict():
    check = _check(_entry(0, errored=29), _entry(29))

    assert check.verdict is None and check.difference is None
    assert check.reason == "Batch A has no result for this check: nothing to compare."


def test_entries_and_checks_in_one_batch_only_are_listed_not_compared():
    shared, only_a, only_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    entries_a = [_entry(29, entry_id=shared, labels=("Check", "Old"), name="shared"),
                 _entry(29, entry_id=only_a, name="removed")]
    entries_b = [_entry(29, entry_id=shared, labels=("Check", "New"), name="shared"),
                 _entry(29, entry_id=only_b, name="added")]

    result = compare.compare(entries_a, entries_b, {"confidence": 0.95})

    assert [(e.name, [c.label for c in e.checks]) for e in result.entries] == [
        ("shared", ["Check"])]
    assert [(u.name, u.label, u.only_in) for u in result.unmatched] == [
        ("shared", "Old", "a"), ("shared", "New", "b"), ("removed", None, "a"),
        ("added", None, "b")]
    assert result.summary == ("No clear difference on the check. 4 entries or checks are in "
                              "one batch only.")


def test_the_summary_leads_with_what_got_worse():
    verdicts = {"better": 1, "worse": 2, "no_difference": 3, "none": 0}

    assert compare.summary(verdicts, []) == "B is worse on 2 of 6 checks and better on 1."
    assert compare.summary({**verdicts, "worse": 0}, []) == (
        "B is better on 1 of 4 checks, worse on none.")


# --- the second wave ---

from assay.models import Comparison, TestTypesModel  # noqa: E402
from assay.schemas.statistics import StatisticalEngine as Test  # noqa: E402

ROUGE = TestTypesModel(name="ROUGE", engine="rouge", comparison=Comparison.gte,
                       config_fields=[{"key": "threshold", "min": 0.0, "max": 1.0}])
CONTAINS = TestTypesModel(name="Contains", engine="contains", comparison=None, config_fields=[])


def _scored(scores, entry_id=None, label="ROUGE") -> BatchEntry:
    runs = [BatchRun(id=uuid.uuid4(), index=i, execution_id=None, status=TestStatus.green,
                     results={label: {"passed": s >= 0.5, "score": s, "detail": None}},
                     error=None)
            for i, s in enumerate(scores, start=1)]
    return BatchEntry(entry_id=entry_id, test_id=None, test_set_id=None, test_set_name=None,
                      name="t", recorded_answer=False,
                      assignments=[TestTypeAssignment(name="ROUGE", label=label)], runs=runs)


_A = [0.61, 0.58, 0.66, 0.55, 0.63, 0.6, 0.57, 0.64, 0.59, 0.62]
_B = [0.7, 0.65, 0.72, 0.61, 0.69, 0.74, 0.66, 0.7]


def _score_check(name, a, b, row=ROUGE):
    return compare.compare_check(name, {"confidence": 0.95}, "ROUGE", "ROUGE", row,
                                 _scored(a), _scored(b))


def test_mean_scores_is_welchs_t_test_with_score_summaries_on_both_sides():
    check = _score_check(Test.mean_scores, _A, _B)

    assert check.verdict == "better"
    assert (check.difference.lower, check.difference.upper) == (0.0395, 0.118)
    assert (check.difference.method, check.p_value_method) == ("t", "welch")
    assert check.p_value == 0.0008
    assert (check.a.scores.mean, check.b.scores.n) == (0.605, 8)


def test_a_lower_is_better_type_reads_a_rise_as_worse():
    lower_better = TestTypesModel(name="ROUGE", engine="rouge", comparison=Comparison.lte,
                                  config_fields=[{"key": "threshold"}])

    check = _score_check(Test.mean_scores, _A, _B, row=lower_better)

    assert check.verdict == "worse"
    assert check.reason.endswith("Lower is better for this check.")


def test_score_ranks_is_mann_whitney_with_the_effect_size():
    check = _score_check(Test.score_ranks, _A, _B)

    assert check.verdict == "better"
    assert check.difference is None
    assert check.effect == 0.925
    assert (check.p_value, check.p_value_method) == (0.0029, "mann_whitney_normal")


def test_score_ranks_needs_four_scores_a_side():
    check = _score_check(Test.score_ranks, [0.1, 0.2, 0.3], [0.7, 0.8, 0.9])

    assert check.verdict is None
    assert "at least 4 scores" in check.reason


def test_comparing_scores_of_a_pass_fail_check_has_no_verdict():
    check = compare.compare_check(Test.mean_scores, {"confidence": 0.95}, "Check",
                                  "Contains", CONTAINS, _entry(20), _entry(28))

    assert check.verdict is None
    assert check.reason.startswith("It only passes or fails")


def test_no_worse_proves_a_margin_with_one_sided_bounds():
    parameters = {"confidence": 0.95, "margin": 0.1}

    proven = compare.compare_check(Test.no_worse, parameters, "Check", "Contains", None,
                                   _entry(29), _entry(29))
    undecided = compare.compare_check(Test.no_worse, parameters, "Check", "Contains", None,
                                      _entry(27), _entry(25))

    assert proven.verdict == "no_worse"
    assert (proven.difference.sides, proven.difference.level) == ("one", 0.95)
    assert undecided.verdict == "inconclusive"
    assert (undecided.difference.lower, undecided.difference.upper) == (-0.2127, 0.0718)
    assert undecided.times_to_decide is not None
    assert compare.summary({"no_worse": 1, "inconclusive": 1}, []) == (
        "B is still as good on 1 of 2 checks; 1 can't be told yet.")


def test_paired_entries_judges_every_pair_together():
    entries_a = [_entry(20 + i, entry_id=uuid.UUID(int=i), name=f"e{i}") for i in range(7)]
    # differences of 9, 8, …, 3 runs in 29: all positive, none tied
    entries_b = [_entry(29, entry_id=uuid.UUID(int=i), name=f"e{i}") for i in range(7)]

    result = compare.compare(entries_a, entries_b, {"confidence": 0.95},
                             Test.paired_entries)

    assert result.paired.n_pairs == 7
    assert result.paired.verdict == "better"
    assert result.paired.wilcoxon_method == "exact"
    assert result.paired.p_value_wilcoxon == 0.0156  # 2 / 2^7
    assert result.verdicts["better"] == 1 and sum(result.verdicts.values()) == 1
    check = result.entries[0].checks[0]
    assert check.verdict is None and check.difference is not None
    assert result.summary.startswith("Entry by entry, B is better: on average it passes")


def test_paired_entries_needs_six_pairs():
    result = compare.compare([_entry(20)], [_entry(28)], {"confidence": 0.95},
                             Test.paired_entries)

    assert result.paired.verdict is None
    assert result.paired.reason == (
        "Only 1 check across the entries ran in both batches; comparing entry by entry needs "
        "at least 6.")
