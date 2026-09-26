import logging

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import DatasetModel, DatasetRowModel
from assay.schemas import DataRowInfo, DataSetImportedData, DataSetImportingData, DataSetInfo
from assay.services.datasets._common import _get_dataset_or_404

logger = logging.getLogger(__name__)


async def upload_new_rows_in_existing_dataset(
        request: DataSetImportingData,
        session: AsyncSession) -> DataSetImportedData:
    """Orchestrates row upload: fetches dataset, appends rows, persists, and returns the result.

    Args:
        request: Request containing the dataset ID and the list of rows to add.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The dataset info (id, name) and the list of newly created row IDs.

    Raises:
        HTTPException 404: No dataset exists with the given ID.
    """
    dataset = await _get_dataset_or_404(request.id, session)

    new_rows = [
        DatasetRowModel(
            input=row.prompt,
            expected_output=row.expected_output,
            model_output=row.model_output,
        )
        for row in request.rows
    ]

    # Load existing rows before extending to avoid MissingGreenlet on the relationship.
    await session.refresh(dataset, attribute_names=['rows'])
    dataset.rows.extend(new_rows)
    # flush() sends INSERTs and populates DB-generated IDs without committing.
    await session.flush()
    result = _build_uploaded_dataset_info(dataset, new_rows)
    await session.commit()

    logger.info(
        "Added %d rows to dataset %s (%r)", len(new_rows), dataset.id, dataset.name,
        extra={"dataset_id": dataset.id, "row_count": len(new_rows)},
    )
    return result


def _build_uploaded_dataset_info(
        dataset: DatasetModel,
        new_rows: list[DatasetRowModel]) -> DataSetImportedData:
    """Build the API response from the dataset and newly inserted rows. Pure function."""
    return DataSetImportedData(
        dataset=DataSetInfo(
            name=dataset.name,
            id=dataset.id,
        ),
        loaded=DataRowInfo(
            n=len(new_rows),
            ids=[row.id for row in new_rows],
        ),
    )
