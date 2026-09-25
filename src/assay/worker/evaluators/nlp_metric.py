from assay.schemas import TestTypeResult


def evaluate(assignment, entry):
    return TestTypeResult(passed=True, score=1.0, detail=None)
