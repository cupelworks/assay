import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base
from assay.models.datasets import DatasetRowModel

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


class TestRunModel(Base):
    """An evaluation run of a dataset row — tracks metric scoring lifecycle."""

    __tablename__ = "test_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Source of truth for input/expected_output/model_output
    dataset_row_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_rows.id"), nullable=True, index=True
    )
    dataset_row: Mapped["DatasetRowModel | None"] = relationship()

    # Which test types to run
    test_type_ids: Mapped[list] = mapped_column(JSON, default=list)

    # Lifecycle
    status: Mapped[TestStatus] = mapped_column(
        SAEnum(TestStatus), default=TestStatus.pending, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    # Results — populated on completion
    scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    statistical_verifications: Mapped[list["StatisticalVerificationModel"]] = relationship(
        back_populates="test_run", cascade="all, delete-orphan"
    )
