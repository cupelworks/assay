import uuid

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base


class SuiteModel(Base):
    __tablename__ = "suites"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)

    cases: Mapped[list["TestCaseModel"]] = relationship(
        back_populates="suite", cascade="all, delete-orphan"
    )


class TestCaseModel(Base):
    __tablename__ = "test_cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    suite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suites.id"), index=True)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text)
    context: Mapped[list | None] = mapped_column(JSON)
    # "metadata" is reserved by SQLAlchemy's DeclarativeBase — mapped to the same column name.
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    suite: Mapped["SuiteModel"] = relationship(back_populates="cases")
