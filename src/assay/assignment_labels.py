"""Labels that tell a test's assignments apart.

A type can be assigned to a test more than once, so each assignment has a
label, unique within the test, and a run's results are keyed by it. Shared by
the API, which labels assignments when they're saved, and the worker, which
labels a frozen copy that has none — so both follow one rule.
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
    taken = {assignment.label.casefold() for assignment in assignments if assignment.label}
    result = []
    for assignment in assignments:
        if not assignment.label:
            label, number = assignment.name, 1
            while label.casefold() in taken:
                number += 1
                label = f"{assignment.name} {number}"
            taken.add(label.casefold())
            assignment = assignment.model_copy(update={"label": label})
        result.append(assignment)
    return result
