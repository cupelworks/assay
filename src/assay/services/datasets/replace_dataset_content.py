# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import logging

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import DatasetRowModel
from assay.schemas import DataSetImportedData, DataSetImportingData
from assay.services.datasets._common import _get_dataset_or_404, _new_rows
from assay.services.datasets.upload_rows_in_dataset import _build_uploaded_dataset_info

logger = logging.getLogger(__name__)


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

    # the new content is numbered from 1, as a new import would be
    new_rows = _new_rows(request.rows, dataset_id=dataset.id)

    session.add_all(new_rows)
    await session.commit()

    logger.info(
        "Replaced the content of dataset %s (%r) with %d rows",
        dataset.id, dataset.name, len(new_rows),
        extra={"dataset_id": dataset.id, "row_count": len(new_rows)},
    )
    return _build_uploaded_dataset_info(dataset, new_rows)
