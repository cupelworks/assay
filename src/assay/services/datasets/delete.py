from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import DatasetModel
from assay.schemas import DataRowInfo, DataSetDeletedInfo, DataSetID, DataSetInfo
from assay.services.datasets._common import _get_dataset_or_404


async def delete_dataset_by_id(
        request: DataSetID,
        session: AsyncSession) -> DataSetDeletedInfo:
    """Orchestrates dataset deletion: fetches, deletes, commits, and returns the deleted info.

    Args:
        request: Request containing the dataset ID to delete.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The deleted dataset info (id, name) and the list of deleted row IDs.

    Raises:
        HTTPException 404: No dataset exists with the given ID.
    """
    dataset = await _get_dataset_or_404(request.id, session)
    await session.delete(dataset)
    await session.commit()

    return _build_deleted_dataset_info(dataset)


def _build_deleted_dataset_info(dataset: DatasetModel) -> DataSetDeletedInfo:
    return DataSetDeletedInfo(
        dataset=DataSetInfo(
            name=dataset.name,
            id=dataset.id,
        ),
        deleted=DataRowInfo(
            n=len(dataset.rows),
            ids=[row.id for row in dataset.rows]
        ),
    )
