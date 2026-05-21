import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from assay.models import DatasetModel


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
