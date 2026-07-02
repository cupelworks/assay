import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status

from assay.models import DatasetModel, DatasetRowModel


async def _check_name_unique(name: str, session: AsyncSession) -> None:
    """Raise 409 if a dataset with the given name already exists.

    scalar() returns the DatasetModel instance if found, None otherwise.
    """
    existing = await session.scalar(select(DatasetModel).where(DatasetModel.name == name))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset name '{name}' already exists.",
        )


async def _get_dataset_or_404(dataset_id: uuid.UUID, session: AsyncSession) -> DatasetModel:
    """Fetch a dataset by ID or raise 404."""
    dataset = await session.scalar(select(DatasetModel).where(DatasetModel.id == dataset_id))
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset with id {dataset_id} not found",
        )
    return dataset


async def _get_all_rows_or_404(
        dataset_id: uuid.UUID,
        session: AsyncSession,) -> list[DatasetRowModel]:
    """Fetch all rows associated with a dataset or raise 404.

    Args:
        dataset_id: UUID of the parent dataset.
        session: Active async database session.

    Returns:
        List of all `DatasetRowModel` instances belonging to the dataset.

    Raises:
        HTTPException: 404 if no rows are found for the given dataset ID.
    """
    rows = list((await session.scalars(
        select(DatasetRowModel)
        .where(DatasetRowModel.dataset_id == dataset_id)
    )).all())
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No Dataset Rows were found with dataset id {dataset_id}",
        )
    return rows


async def _get_rows_or_404(
        row_ids: list[uuid.UUID],
        session: AsyncSession) -> list[DatasetRowModel]:
    """Fetch rows by IDs (with their dataset) or raise 404 if any are missing."""
    rows = (await session.scalars(
        select(DatasetRowModel)
        .where(DatasetRowModel.id.in_(row_ids))
        .options(selectinload(DatasetRowModel.dataset))
    )).all()

    missing_ids = set(row_ids) - {row.id for row in rows}
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Row ids not found: {[str(row_id) for row_id in missing_ids]}",
        )

    return list(rows)
