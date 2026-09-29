from types import SimpleNamespace

from assay.services.tests._common import _frozen_test_type_assignments


def test_every_snapshot_copies_each_assignments_answer_path():
    test = SimpleNamespace(test_type_assignments=[
        SimpleNamespace(test_type_name="Contains", config={"substring": "x"},
                        answer_path="$.result.category"),
        SimpleNamespace(test_type_name="ROUGE", config={"threshold": "0.5"}, answer_path=None),
    ])

    assert _frozen_test_type_assignments(test) == [
        {"name": "Contains", "config": {"substring": "x"}, "answer_path": "$.result.category"},
        {"name": "ROUGE", "config": {"threshold": "0.5"}, "answer_path": None},
    ]
