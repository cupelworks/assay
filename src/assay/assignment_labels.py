"""Labels that tell a test's assignments apart.

A type can be assigned to a test more than once, so each assignment has a
label, unique within the test, and a run's results are keyed by it. Shared by
the API, which labels assignments when they're saved, and the worker, which
labels a frozen copy that has none — so both follow one rule.

A test's assignments are listed in label order, ignoring letter case, the
same way labels are compared: wherever they're saved and wherever the API
returns them. The order is applied here, in Python, not by the database: a
table has no order of its own, and SQLite's lower() folds only ASCII
letters while PostgreSQL's folds all of Unicode, so a database sort would
differ between the two.
"""
from assay.schemas import TestTypeAssignment


def labelled(assignments: list[TestTypeAssignment]) -> list[TestTypeAssignment]:
    """Every assignment with its label, in the order given.

    A label sent is kept as it is, so a client that sends back what a GET
    returned keeps each check's identity when it replaces the list — and its
    results stay comparable across runs. An assignment sent without one is
    named after its type, numbered when that's taken ("Contains",
    "Contains 2", ...), skipping every label already in use, sent or
    generated. Labels are compared ignoring letter case, as they read the
    same in the UI. Duplicate labels sent are refused before this, by the
    API's validation.
    """
    taken = {label_key(assignment.label) for assignment in assignments if assignment.label}
    result = []
    for assignment in assignments:
        if not assignment.label:
            label, number = assignment.name, 1
            while label_key(label) in taken:
                number += 1
                label = f"{assignment.name} {number}"
            taken.add(label_key(label))
            assignment = assignment.model_copy(update={"label": label})
        result.append(assignment)
    return result


def label_key(label: str) -> str:
    """What labels are compared and ordered by: the label, ignoring case."""
    return label.casefold()


def in_label_order(assignments: list[TestTypeAssignment]) -> list[TestTypeAssignment]:
    """The assignments sorted by label, ignoring letter case — the one order
    a test's assignments are saved and returned in. Labels are unique that
    way, so the order is total."""
    return sorted(assignments, key=lambda assignment: label_key(assignment.label))
