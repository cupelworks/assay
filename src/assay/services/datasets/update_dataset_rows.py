from sqlalchemy.ext.asyncio import AsyncSession

from assay.schemas import DataRowInfo, DataSetInfo, DataSetRow, DataSetRowUpdatedData
from assay.services.datasets._common import _get_rows_or_404


async def update_dataset_rows_by_id(
        request: list[DataSetRow],
        session: AsyncSession) -> DataSetRowUpdatedData:
    """Orchestrates row update: validates IDs, mutates attributes, commits, and returns the result.

    Args:
        request: List of row updates, each containing the row `id` and the new `row_info`.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The dataset info (id, name) and the list of updated row IDs.

    Raises:
        HTTPException 404: One or more row IDs were not found — no rows are updated.
    """

    row_ids = [row.id for row in request]
    row_models = await _get_rows_or_404(row_ids, session)
    dataset = row_models[0].dataset

    row_models_by_id = {row.id: row for row in row_models}

    for change in request:
        row_model = row_models_by_id[change.id]
        row_model.input = change.row_info.prompt
        row_model.model_output = change.row_info.model_output
        row_model.expected_output = change.row_info.expected_output

    await session.commit()

    return DataSetRowUpdatedData(
        dataset=DataSetInfo(id=dataset.id, name=dataset.name),
        updated=DataRowInfo(n=len(row_models), ids=[row.id for row in row_models]),
    )
