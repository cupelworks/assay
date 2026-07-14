from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import DatasetRowModel
from assay.schemas import DataSetImportedData, DataSetImportingData
from assay.services.datasets._common import _get_dataset_or_404
from assay.services.datasets.upload_rows_in_dataset import _build_uploaded_dataset_info


async def replace_dataset_content_by_dataset_id(
        request: DataSetImportingData,
        session: AsyncSession) -> DataSetImportedData:
    """Orchestrates dataset content replacement: validates ID, deletes existing rows,
    inserts new ones, commits, and returns the result.

    Args:
        request: Request containing the dataset `id` and the new list of `rows`.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The dataset info (id, name) and the list of newly created row IDs.

    Raises:
        HTTPException 404: No dataset exists with the given ID.
    """

    dataset = await _get_dataset_or_404(request.id, session)

    # No guard needed against tests created from the old rows here: TestModel's
    # dataset_row_id FK has ondelete="SET NULL", so any referencing test just
    # loses its traceability pointer instead of blocking this delete.
    await session.execute(
        delete(DatasetRowModel)
        .where(DatasetRowModel.dataset_id == dataset.id)
    )

    new_rows = [
        DatasetRowModel(
            dataset_id=dataset.id,
            input=row.prompt,
            expected_output=row.expected_output,
            model_output=row.model_output,
        )
        for row in request.rows
    ]

    session.add_all(new_rows)
    await session.commit()

    return _build_uploaded_dataset_info(dataset, new_rows)
