from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import DatasetRowModel
from assay.schemas import DataRowInfo, DataSetDeletedData, DataSetDeletingData, DataSetInfo
from assay.services.datasets._common import _get_rows_or_404


async def delete_dataset_rows_by_ids(
        request: DataSetDeletingData,
        session: AsyncSession) -> DataSetDeletedData: 
    """Orchestrates row deletion: validates IDs, deletes, commits, and returns the deleted info.

    Args:
        request: Request containing the list of row IDs to delete.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The dataset info (id, name) and the list of deleted row IDs.

    Raises:
        HTTPException 404: One or more row IDs were not found — no rows are deleted.
    """

    rows_model = await _get_rows_or_404(request.row_ids, session)

    result = _build_deleted_rows_info(rows_model)
    
    await session.execute(delete(DatasetRowModel).where(DatasetRowModel.id.in_(request.row_ids)))
    await session.commit()

    return result


def _build_deleted_rows_info(rows_model: list[DatasetRowModel]) -> DataSetDeletedData:
    return DataSetDeletedData(
        dataset=DataSetInfo(
            name=rows_model[0].dataset.name,
            id=rows_model[0].dataset_id,
        ),
        deleted=DataRowInfo(
            n=len(rows_model),
            ids=[row.id for row in rows_model]
        ),
    )
