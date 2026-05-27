import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base

if TYPE_CHECKING:
    from assay.models.stats import StatisticalVerificationModel


class TestStatus(StrEnum):
    pending = "Pending"
    running = "Running"
    completed = "Completed"
    failed = "Failed"


class TestTypes(StrEnum):
    deterministic = "Deterministic"
    nlp_metric = "NLP Metric"
    llm_as_judge = "LLM-As-Judge"


class TestTypesCost(StrEnum):
    very_fast = "Free & Lightning Fast"
    fast = "Free & Fast"
    expensive = "Expensive & Slow"


class TestTypesModel(Base):
    __tablename__ = "test_types"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    category: Mapped[str] = mapped_column(SAEnum(TestTypes), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    best_for: Mapped[str] = mapped_column(Text, nullable=True)
    cost: Mapped[str] = mapped_column(SAEnum(TestTypesCost), nullable=True)
    limitations: Mapped[str] = mapped_column(Text, nullable=True)
    required_reference: Mapped[bool] = mapped_column(Boolean, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=True,
                                                 default=lambda: datetime.now().astimezone())


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