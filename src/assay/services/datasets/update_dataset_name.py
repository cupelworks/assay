import logging

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import DatasetModel
from assay.schemas import DataSetInfo
from assay.services.datasets._common import _check_name_unique, _get_dataset_or_404

logger = logging.getLogger(__name__)


async def update_dataset_name_by_id(
        request: DataSetInfo,
        session: AsyncSession) -> DataSetInfo:  # pragma: no cover
    """Orchestrates dataset rename: fetches, validates uniqueness, persists, and returns the result.

    Args:
        request: Request containing the dataset ID and the desired new name.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The updated dataset info (id, name).

    Raises:
        HTTPException 404: No dataset exists with the given ID.
        HTTPException 409: A dataset with the new name already exists.
    """
    dataset = await _get_dataset_or_404(request.id, session)
    previous_name = dataset.name
    renamed = previous_name != request.name
    if renamed:
        await _check_name_unique(request.name, session)
    await _apply_name_update(dataset, request.name, session)
    if renamed:
        logger.info(
            "Renamed dataset %s from %r to %r", dataset.id, previous_name, request.name,
            extra={"dataset_id": dataset.id},
        )
    return _build_dataset_info(dataset)


async def _apply_name_update(dataset: DatasetModel, name: str, session: AsyncSession) -> None:
    """Set a new name on a dataset and commit. Isolated for testability."""
    dataset.name = name
    await session.commit()


def _build_dataset_info(dataset: DatasetModel) -> DataSetInfo:
    """Build a DataSetInfo response from a DatasetModel. Pure function — fully unit-testable."""
    return DataSetInfo(id=dataset.id, name=dataset.name)
