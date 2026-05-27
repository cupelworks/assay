import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base

# Imported only during static analysis (Pyright/mypy), never at runtime.
# Avoids a circular import: stats.py → test.py → stats.py.
# At runtime, SQLAlchemy resolves "TestRunModel" from its own mapper registry
# (populated when models/__init__.py imports both modules), so no Python import is needed.
if TYPE_CHECKING:
    from assay.models.test import TestRunModel


class StatisticalVerificationModel(Base):
    """Result of a statistical test (e.g. z-test) run against an executed test's scores."""

    __tablename__ = "statistical_verifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    executed_test_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_runs.id"), index=True, nullable=True
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
    verified_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    test_run: Mapped["TestRunModel"] = relationship(
        back_populates="statistical_verifications"
    )