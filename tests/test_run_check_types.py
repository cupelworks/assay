import uuid

from assay.models import StandaloneRunModel, TestRunCheckTypeModel, TestRunModel
from assay.run_check_types import asked_checks, check_types, with_check_types

CONTAINS = {"name": "Contains", "label": "Contains", "config": {"substring": "a"}}
CONTAINS_2 = {"name": "Contains", "label": "Mentions B", "config": {"substring": "b"}}
TOXICITY = {"name": "Toxicity", "label": "Toxicity", "config": None}


def test_each_type_counts_once_and_the_names_are_sorted():
    assert check_types([TOXICITY, CONTAINS, CONTAINS_2]) == ["Contains", "Toxicity"]


def test_a_skipped_label_leaves_its_type_out_only_if_no_other_check_has_it():
    assert check_types([CONTAINS, CONTAINS_2, TOXICITY], ["Toxicity"]) == ["Contains"]
    assert check_types([CONTAINS, CONTAINS_2], ["Contains"]) == ["Contains"]
    assert check_types([CONTAINS, CONTAINS_2], ["Contains", "Mentions B"]) == []


def test_no_assignments_ask_nothing():
    assert check_types([]) == []
    assert check_types([], None) == []


def _names(run: TestRunModel) -> list[str]:
    assert all(isinstance(row, TestRunCheckTypeModel) and row.run_id == run.id
               for row in run.check_types)
    return [row.test_type_name for row in run.check_types]


def test_a_standalone_run_reads_its_own_copy():
    run = TestRunModel(id=uuid.uuid4(), test_id=uuid.uuid4())
    run.standalone_run = StandaloneRunModel(id=run.id, name="t", input="q",
                                            test_type_assignments=[TOXICITY, CONTAINS])

    with_check_types([run])

    assert _names(run) == ["Contains", "Toxicity"]


def test_an_entry_run_reads_its_entrys_checks_without_its_skipped_labels():
    entry_id = uuid.uuid4()
    run = TestRunModel(id=uuid.uuid4(), test_set_entry_id=entry_id, skip_labels=["Toxicity"])

    with_check_types([run], {entry_id: [CONTAINS, TOXICITY]})

    assert _names(run) == ["Contains"]


def test_it_replaces_what_a_run_had():
    entry_id = uuid.uuid4()
    run = TestRunModel(id=uuid.uuid4(), test_set_entry_id=entry_id)
    with_check_types([run], {entry_id: [CONTAINS, TOXICITY]})

    run.skip_labels = ["Toxicity"]
    with_check_types([run], {entry_id: [CONTAINS, TOXICITY]})

    assert _names(run) == ["Contains"]


def test_the_checks_a_run_asks_leave_out_its_skipped_labels():
    assert asked_checks([CONTAINS, CONTAINS_2, TOXICITY], ["Mentions B"]) == [CONTAINS, TOXICITY]
    assert asked_checks(None) == []
