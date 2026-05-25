from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from assay.models import DatasetRowModel
from assay.schemas import DataRowInfo, DataSetDeletedData, DataSetDeletingData, DataSetInfo


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

    rows_model = (await session.scalars(select(DatasetRowModel)
                                        .where(DatasetRowModel.id.in_(request.row_ids))
                                        .options(selectinload(DatasetRowModel.dataset))
                                        )).all()

    found_ids = [row.id for row in rows_model]
    missing_ids = set(request.row_ids) - set(found_ids)

    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Row ids not found: {[str(row_id) for row_id in missing_ids]}",
        )

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
