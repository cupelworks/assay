import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base


class DatasetModel(Base):
    """A named dataset — groups multiple DatasetRowModel entries under one identity."""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    rows: Mapped[list["DatasetRowModel"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class DatasetRowModel(Base):
    """A single row belonging to a dataset — mirrors one line of a .jsonl file."""

    __tablename__ = "dataset_rows"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"), index=True)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str] = mapped_column(Text)
    model_output: Mapped[str] = mapped_column(Text)

    dataset: Mapped["DatasetModel"] = relationship(back_populates="rows")
