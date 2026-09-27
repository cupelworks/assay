from assay.schemas import EvaluationInput, TestTypeResult


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    # Stub until Phase 7 — same fixed outcome the llm_as_judge category module
    # returned, so a run's results don't change across the restructure.
    return TestTypeResult(passed=True, score=None, detail="Testing")
