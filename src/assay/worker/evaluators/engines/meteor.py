from assay.schemas import EvaluationInput, TestTypeResult


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    # Stub: one fixed outcome regardless of input, until the real engine is
    # written.
    return TestTypeResult(passed=True, score=1.0, detail=None)
