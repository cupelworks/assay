# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base
from assay.timestamps import utc_now


class DatasetModel(Base):
    """A named dataset — groups multiple DatasetRowModel entries under one identity."""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now
    )

    rows: Mapped[list["DatasetRowModel"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class DatasetRowModel(Base):
    """A single row belonging to a dataset — mirrors one line of a .jsonl file."""

    __tablename__ = "dataset_rows"
    __table_args__ = (
        UniqueConstraint("dataset_id", "position", name="uq_dataset_rows_dataset_id_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"), index=True)
    # The row's number within its dataset, from 1: an import numbers its lines,
    # added rows go after the highest, and a deleted row leaves a gap, so a row
    # and the tests made from it keep the same number.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str] = mapped_column(Text)
    model_output: Mapped[str] = mapped_column(Text)

    dataset: Mapped["DatasetModel"] = relationship(back_populates="rows")
