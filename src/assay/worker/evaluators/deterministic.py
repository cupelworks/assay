from assay.schemas import TestTypeResult


def evaluate(assignment, entry):
    return TestTypeResult(passed=True, score=None, detail=None)
