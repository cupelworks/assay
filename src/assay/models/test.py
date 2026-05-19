import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base

if TYPE_CHECKING:
    from assay.models.stats import StatisticalVerificationModel


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
    expected_output: Mapped[str] = mapped_column(Text)
    model_output: Mapped[str] = mapped_column(Text)
    # Metric names to compute (e.g. ["rouge", "bertscore"]).
    metrics: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[TestStatus] = mapped_column(SAEnum(TestStatus), default=TestStatus.pending)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

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
    executed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    pending_test: Mapped["PendingTestModel"] = relationship(back_populates="result")
    statistical_verifications: Mapped[list["StatisticalVerificationModel"]] = relationship(
        back_populates="executed_test", cascade="all, delete-orphan"
    )