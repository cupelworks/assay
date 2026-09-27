from assay.schemas import EvaluationInput, TestTypeResult


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    # Stub until Phase 5 — same fixed outcome the nlp_metric category module
    # returned, so a run's results don't change across the restructure.
    return TestTypeResult(passed=True, score=1.0, detail=None)
