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
    assert check.reason.startswith("B passes less often than A: 30% against 90%")


def test_no_real_difference_says_how_big_two_new_batches_would_need_to_be():
    check = _check(_entry(27), _entry(28))

    assert check.verdict == "no_difference"
    assert check.times_to_decide == 647
    assert check.times_to_decide_message == (
        "Two new batches of about 647 times each would likely tell 93.1% from 96.55%.")


def test_equal_rates_can_never_be_told_apart():
    check = _check(_entry(29), _entry(29))

    assert check.verdict == "no_difference"
    assert check.times_to_decide is None
    assert "no batch size would show a difference" in check.times_to_decide_message


def test_errored_runs_are_left_out_of_both_samples():
    check = _check(_entry(20, errored=5), _entry(29))

    assert check.a.counts.evaluated == 24 and check.a.counts.errored == 5


def test_a_side_with_nothing_evaluated_has_no_verdict():
    check = _check(_entry(0, errored=29), _entry(29))

    assert check.verdict is None and check.difference is None
    assert check.reason == "Batch A has no evaluated run of this check: nothing to compare."


def test_entries_and_checks_in_one_batch_only_are_listed_not_compared():
    shared, only_a, only_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    entries_a = [_entry(29, entry_id=shared, labels=("Check", "Old"), name="shared"),
                 _entry(29, entry_id=only_a, name="removed")]
    entries_b = [_entry(29, entry_id=shared, labels=("Check", "New"), name="shared"),
                 _entry(29, entry_id=only_b, name="added")]

    result = compare.compare(entries_a, entries_b, 0.95)

    assert [(e.name, [c.label for c in e.checks]) for e in result.entries] == [
        ("shared", ["Check"])]
    assert [(u.name, u.label, u.only_in) for u in result.unmatched] == [
        ("shared", "Old", "a"), ("shared", "New", "b"), ("removed", None, "a"),
        ("added", None, "b")]
    assert result.summary == ("No real difference on any of the 1 check at this size. 4 "
                              "entries or checks are in one batch only.")


def test_the_summary_leads_with_what_got_worse():
    verdicts = {"better": 1, "worse": 2, "no_difference": 3, "none": 0}

    assert compare.summary(verdicts, []) == "B is worse on 2 of 6 checks and better on 1."
    assert compare.summary({**verdicts, "worse": 0}, []) == (
        "B is better on 1 of 4 checks, worse on none.")
