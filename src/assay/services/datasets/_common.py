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
