import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import DatasetModel
from assay.schemas import DataSetMetadata, PaginatedDataSetResponse
from assay.services.datasets._common import _get_dataset_or_404


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

    total = await session.scalar(select(func.count(DatasetModel.id))) or 0
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


async def get_dataset_metadata_by_id(
        dataset_id: uuid.UUID,
        session: AsyncSession) -> DataSetMetadata:
    """Orchestrates single dataset retrieval: validates ID and returns its metadata.

    Args:
        dataset_id: The UUID of the dataset to retrieve.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The dataset metadata (id, name, created_at).

    Raises:
        HTTPException 404: No dataset exists with the given ID.
    """

    dataset = await _get_dataset_or_404(dataset_id, session)

    return DataSetMetadata(
        id=dataset.id,
        name=dataset.name,
        created_at=dataset.created_at,
    )
