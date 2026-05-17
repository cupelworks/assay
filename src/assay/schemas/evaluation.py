from pydantic import BaseModel, Field


class JudgeCriterion(BaseModel):
    """A rubric item the LLM-as-judge scores each response against."""

    name: str
    description: str
    scale_min: float = 1.0
    scale_max: float = 5.0


class EvaluationRequest(BaseModel):
    """Ad-hoc evaluation: score a single (input, expected, actual) triple."""

    input: str
    actual_output: str
    expected_output: str | None = None
    metrics: list[str] = Field(default_factory=list)
    judge_criteria: list[JudgeCriterion] = Field(default_factory=list)
