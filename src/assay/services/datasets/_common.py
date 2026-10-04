import uuid
from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status

from assay.models import DatasetModel, DatasetRowModel, TestModel
from assay.schemas import DataSetMetadata, DataSetRowRead, DataSetRowSchema
from assay.services._standing import count_by, latest_per


async def _check_name_unique(name: str, session: AsyncSession) -> None:
    """Raise 409 if a dataset with the given name already exists.

    scalar() returns the DatasetModel instance if found, None otherwise.
    """
    existing = await session.scalar(select(DatasetModel).where(DatasetModel.name == name))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset name '{name}' already exists.",
        )


async def _get_dataset_or_404(dataset_id: uuid.UUID, session: AsyncSession) -> DatasetModel:
    """Fetch a dataset by ID or raise 404."""
    dataset = await session.scalar(select(DatasetModel).where(DatasetModel.id == dataset_id))
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset with id {dataset_id} not found",
        )
    return dataset


async def _get_all_rows_or_404(
        dataset_id: uuid.UUID,
        session: AsyncSession,
        row_ids: list[uuid.UUID] | None = None) -> list[DatasetRowModel]:
    """Fetch a dataset's rows by number, all or only `row_ids`, or raise.

    Args:
        dataset_id: UUID of the parent dataset.
        session: Active async database session.
        row_ids: Only these rows, when given.

    Returns:
        The `DatasetRowModel` instances, in row-number order.

    Raises:
        HTTPException: 422 if a row id isn't one of the dataset's rows, naming
            each; 404 if no rows are found for the given dataset ID.
    """
    query = select(DatasetRowModel).where(DatasetRowModel.dataset_id == dataset_id)
    if row_ids:
        query = query.where(DatasetRowModel.id.in_(row_ids))
    rows = list((await session.scalars(query.order_by(DatasetRowModel.position))).all())
    missing = [str(row_id) for row_id in dict.fromkeys(row_ids or [])
               if row_id not in {row.id for row in rows}]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Rows {missing} aren't rows of dataset {dataset_id}",
        )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No Dataset Rows were found with dataset id {dataset_id}",
        )
    return rows


async def _get_rows_or_404(
        row_ids: list[uuid.UUID],
        session: AsyncSession) -> list[DatasetRowModel]:
    """Fetch rows by IDs (with their dataset) or raise 404 if any are missing."""
    rows = (await session.scalars(
        select(DatasetRowModel)
        .where(DatasetRowModel.id.in_(row_ids))
        .options(selectinload(DatasetRowModel.dataset))
    )).all()

    missing_ids = set(row_ids) - {row.id for row in rows}
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Row ids not found: {[str(row_id) for row_id in missing_ids]}",
        )

    return list(rows)


def _new_rows(rows: Iterable[DataSetRowSchema], first: int = 1,
              dataset_id: uuid.UUID | None = None) -> list[DatasetRowModel]:
    """Dataset rows from validated rows, numbered from `first` in their order.
    `dataset_id` may be left out when the rows are attached to a new dataset."""
    return [
        DatasetRowModel(dataset_id=dataset_id, position=first + index, input=row.prompt,
                        expected_output=row.expected_output, model_output=row.model_output)
        for index, row in enumerate(rows)
    ]


async def _next_position(dataset_id: uuid.UUID, session: AsyncSession) -> int:
    """The number an added row gets: one past the dataset's highest. Gaps
    left by deleted rows are never filled; a deleted highest number can come
    back, harmlessly, since tests made from a deleted row lose their link."""
    highest = await session.scalar(
        select(func.max(DatasetRowModel.position)).where(DatasetRowModel.dataset_id == dataset_id))
    return (highest or 0) + 1


async def _describe_datasets(datasets: list[DatasetModel],
                             session: AsyncSession) -> list[DataSetMetadata]:
    """Datasets as they're read: their rows counted, and the first row's prompt."""
    ids = [dataset.id for dataset in datasets]
    rows = await count_by(session, DatasetRowModel.dataset_id, ids)
    first = await latest_per(session, DatasetRowModel.dataset_id, ids, (DatasetRowModel.input,),
                             (DatasetRowModel.position,))
    return [DataSetMetadata(id=dataset.id, name=dataset.name, created_at=dataset.created_at,
                            row_count=rows[dataset.id],
                            first_prompt=first[dataset.id].input if dataset.id in first else None)
            for dataset in datasets]


async def _describe_rows(rows: list[DatasetRowModel],
                         session: AsyncSession) -> list[DataSetRowRead]:
    """Dataset rows as they're read: their content, their number, and how many
    tests were made from each."""
    tests = await count_by(session, TestModel.dataset_row_id, [row.id for row in rows])
    return [DataSetRowRead(id=row.id, number=row.position, test_count=tests[row.id],
                           row_info=DataSetRowSchema(prompt=row.input,
                                                     expected_output=row.expected_output,
                                                     model_output=row.model_output))
            for row in rows]
