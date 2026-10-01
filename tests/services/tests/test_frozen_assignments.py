from types import SimpleNamespace

from assay.services.tests._common import _frozen_test_type_assignments


def test_every_snapshot_copies_each_assignments_label_and_answer_path():
    test = SimpleNamespace(test_type_assignments=[
        SimpleNamespace(test_type_name="Contains", label="Mentions the category",
                        config={"substring": "x"}, answer_path="$.result.category"),
        SimpleNamespace(test_type_name="Contains", label="Contains 2",
                        config={"substring": "y"}, answer_path=None),
    ])

    assert _frozen_test_type_assignments(test) == [
        {"name": "Contains", "label": "Mentions the category", "config": {"substring": "x"},
         "answer_path": "$.result.category"},
        {"name": "Contains", "label": "Contains 2", "config": {"substring": "y"},
         "answer_path": None},
    ]
