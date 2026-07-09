import uuid

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from assay.models import DatasetRowModel
from assay.schemas import DataSetRow, DataSetRowSchema, PaginatedDataSetRowResponse
from assay.services.datasets._common import _get_dataset_or_404


async def get_dataset_rows_by_id(
        dataset_id: uuid.UUID,
        session: AsyncSession,
        offset: int,
        limit: int) -> PaginatedDataSetRowResponse:
    """Orchestrates dataset row listing: validates the dataset ID, counts total rows,
    fetches the requested page, and returns a paginated response.

    Args:
        dataset_id: The UUID of the dataset whose rows to retrieve.
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with the dataset ID, row data, total count, offset, and limit.

    Raises:
        HTTPException 404: No dataset exists with the given ID.
    """

    _ = await _get_dataset_or_404(dataset_id, session)

    total = await session.scalar(select(func.count(DatasetRowModel.dataset_id))
                                 .where(DatasetRowModel.dataset_id == dataset_id)) or 0
    rows = await session.scalars(select(DatasetRowModel)
                                 .where(DatasetRowModel.dataset_id == dataset_id)
                                 .offset(offset).limit(limit))

    return PaginatedDataSetRowResponse(
        id=dataset_id,
        items=[
            DataSetRow(
                id=row.id,
                row_info=DataSetRowSchema(
                    prompt=row.input,
                    expected_output=row.expected_output,
                    model_output=row.model_output,
                )
            )
            for row in rows
        ],
        total=total,
        offset=offset,
        limit=limit,
    )
