from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import DatasetModel
from assay.schemas import DataSetMetadata, PaginatedDataSetResponse


async def get_datasets_metadata(
        offset: int,
        limit: int,
        session: AsyncSession) -> PaginatedDataSetResponse:
    """Orchestrates dataset listing: counts total records, fetches the requested page,
    and returns a paginated response.

    Args:
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        A paginated response with dataset metadata, total count, offset, and limit.
    """

    total = await session.scalar(select(func.count(DatasetModel.id)).select_from(DatasetModel))
    datasets = await session.scalars(select(DatasetModel)
                                     .offset(offset).limit(limit))
    
    return PaginatedDataSetResponse(
        items=[
            DataSetMetadata(
                id=dataset.id,
                name=dataset.name,
                created_at=dataset.created_at,
            )
            for dataset in datasets
        ],
        total=total,
        offset=offset,
        limit=limit,
    )
