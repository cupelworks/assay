from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MetricScore(BaseModel):
    """Result of one metric evaluated on one test case."""

    metric: str
    value: float
    details: dict[str, Any] = Field(default_factory=dict)


class TestCaseResult(BaseModel):
    """Outcome of running one test case end-to-end (target + evaluators)."""

    case_id: UUID
    actual_output: str
    scores: list[MetricScore] = Field(default_factory=list)
    latency_ms: float | None = None
    error: str | None = None


class StatisticalSummary(BaseModel):
    """Aggregated statistics for a single metric across a run."""

    metric: str
    count: int
    mean: float
    stdev: float
    min: float
    max: float
    p50: float
    p95: float


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class EvaluationRun(BaseModel):
    """A single execution of a test suite against a target system."""

    id: UUID = Field(default_factory=uuid4)
    suite_id: UUID
    status: RunStatus = RunStatus.pending
    started_at: datetime | None = None
    completed_at: datetime | None = None
    results: list[TestCaseResult] = Field(default_factory=list)
    summary: list[StatisticalSummary] = Field(default_factory=list)
