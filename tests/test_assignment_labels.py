import pytest

from assay.assignment_labels import labelled
from assay.schemas import TestTypeAssignment


def _labels(*assignments):
    return [a.label for a in labelled([TestTypeAssignment(**a) for a in assignments])]


def test_an_assignment_without_a_label_is_named_after_its_type():
    assert _labels({"name": "Contains"}, {"name": "ROUGE"}) == ["Contains", "ROUGE"]


def test_a_repeated_type_is_numbered_in_order():
    assert _labels({"name": "Contains"}, {"name": "Contains"}, {"name": "Contains"}) == [
        "Contains", "Contains 2", "Contains 3"]


def test_a_label_sent_is_kept_and_generated_ones_skip_it():
    # the second Contains was saved as "Contains 2"; the client sends it back
    # and adds a new one: the new one must not take "Contains 2" again
    assert _labels({"name": "Contains", "label": "Contains 2"}, {"name": "Contains"},
                   {"name": "Contains"}) == ["Contains 2", "Contains", "Contains 3"]


def test_removing_the_first_of_two_leaves_the_seconds_label_alone():
    assert _labels({"name": "Contains", "label": "Contains 2"}) == ["Contains 2"]


def test_a_custom_label_is_kept_and_frees_the_types_name():
    assert _labels({"name": "Contains", "label": "Mentions the refund"},
                   {"name": "Contains"}) == ["Mentions the refund", "Contains"]


def test_labels_are_compared_ignoring_letter_case():
    assert _labels({"name": "Contains", "label": "contains"}, {"name": "Contains"}) == [
        "contains", "Contains 2"]


@pytest.mark.parametrize("label", ["", "   ", None])
def test_a_blank_label_counts_as_not_set(label):
    assert TestTypeAssignment(name="Contains", label=label).label is None


def test_a_label_is_trimmed_and_bounded():
    assert TestTypeAssignment(name="Contains", label="  Refund  ").label == "Refund"
    with pytest.raises(ValueError):
        TestTypeAssignment(name="Contains", label="x" * 101)
