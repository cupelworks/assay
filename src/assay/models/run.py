import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base
from assay.schemas.run import RunStatus


class EvaluationRunModel(Base):
    """One execution of a TestSuite — tracks lifecycle (pending → running → completed/failed)."""

    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Indexed — runs are frequently filtered by suite when fetching history.
    suite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suites.id"), index=True)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.pending)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Deleting a run removes all its results; scores cascade further via TestCaseResultModel.
    results: Mapped[list["TestCaseResultModel"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class TestCaseResultModel(Base):
    """Output and scores for one test case; may belong to a run or be a standalone ad-hoc eval."""

    __tablename__ = "test_case_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Nullable — ad-hoc evaluations (POST /evaluations) are not tied to a run.
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    # case_id is a logical reference only — TestCaseModel rows may be deleted independently.
    case_id: Mapped[uuid.UUID]
    actual_output: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)

    run: Mapped["EvaluationRunModel | None"] = relationship(back_populates="results")
    scores: Mapped[list["MetricScoreModel"]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class MetricScoreModel(Base):
    """Score produced by one evaluator (NLP metric or LLM judge) for one test case result."""

    __tablename__ = "metric_scores"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Indexed — scores are always fetched in bulk by result, never individually.
    result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_case_results.id"), index=True
    )
    metric: Mapped[str] = mapped_column(String(255))
    value: Mapped[float] = mapped_column(Float)
    # Stores evaluator-specific breakdown (e.g. judge reasoning, token counts).
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    result: Mapped["TestCaseResultModel"] = relationship(back_populates="scores")
