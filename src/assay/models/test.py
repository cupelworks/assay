import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base


class TestStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class PendingTestModel(Base):
    """A test queued for execution — holds the input and the metrics to evaluate."""

    __tablename__ = "pending_tests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text)
    # Metric names to compute (e.g. ["rouge", "bertscore"]).
    metrics: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[TestStatus] = mapped_column(SAEnum(TestStatus), default=TestStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now().astimezone())

    result: Mapped["ExecutedTestModel | None"] = relationship(
        back_populates="pending_test", uselist=False
    )


class ExecutedTestModel(Base):
    """The outcome of a completed test — actual output and per-metric scores."""

    __tablename__ = "executed_tests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pending_test_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pending_tests.id"), index=True
    )
    actual_output: Mapped[str] = mapped_column(Text)
    # {metric_name: score} — e.g. {"rouge": 0.81, "bertscore": 0.74}
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now().astimezone())

    pending_test: Mapped["PendingTestModel"] = relationship(back_populates="result")
    statistical_verifications: Mapped[list["StatisticalVerificationModel"]] = relationship(
        back_populates="executed_test", cascade="all, delete-orphan"
    )


class StatisticalVerificationModel(Base):
    """Result of a statistical test (e.g. z-test) run against an executed test's scores."""

    __tablename__ = "statistical_verifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    executed_test_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("executed_tests.id"), index=True
    )
    # The metric these scores belong to (e.g. "rouge").
    metric: Mapped[str] = mapped_column(String(255))
    test_type: Mapped[str] = mapped_column(String(50))  # e.g. "z-test"
    threshold: Mapped[float] = mapped_column(Float)
    alpha: Mapped[float] = mapped_column(Float)
    alternative: Mapped[str] = mapped_column(String(20))
    z_statistic: Mapped[float] = mapped_column(Float)
    p_value: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool]
    # Full result payload for auditability.
    result_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now().astimezone())

    executed_test: Mapped["ExecutedTestModel"] = relationship(
        back_populates="statistical_verifications"
    )