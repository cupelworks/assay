from io import TextIOWrapper
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from assay.models import DatasetModel, DatasetRowModel
from assay.schemas import (
    DataRowInfo,
    DataSetImportViaPathRequest,
    DataSetImportViaPathResponse,
    DataSetInfo,
    DataSetRowSchema,
)
from assay.services.datasets._common import _check_name_unique


async def upload_dataset_via_path(req: DataSetImportViaPathRequest,
                                  session: AsyncSession
                                  ) -> DataSetImportViaPathResponse:  # pragma: no cover
    """Orchestrates dataset upload: validates, parses, persists, and returns the result.

    Args:
        req: Request containing the absolute file path and an optional dataset name.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The created dataset info (id, name) and the list of generated row IDs.

    Raises:
        HTTPException 404: File does not exist at the given path.
        HTTPException 409: A dataset with the same name already exists.
        HTTPException 422: One or more lines fail schema validation.
    """
    _check_file_exists(req.path)
    await _check_name_unique(req.dataset_name, session)

    with open(req.path) as f:
        rows, errors = _parse_and_validate_rows(f)

    _raise_if_errors(errors)

    dataset = _build_dataset_model(req.dataset_name, rows)
    dataset_id, row_ids = await _persist_dataset(dataset, session)

    return _build_response(req, rows, dataset_id, row_ids)


async def _persist_dataset(
    dataset: DatasetModel, session: AsyncSession
) -> tuple[object, list[object]]:
    """Persist a DatasetModel and return (dataset_id, row_ids).

    flush() sends the INSERT and populates DB-generated IDs without committing.
    IDs are captured while objects are still live — avoids lazy-load issues after commit.
    If commit fails, the open transaction is rolled back automatically.
    """
    session.add(dataset)
    await session.flush()
    row_ids = [r.id for r in dataset.rows]
    dataset_id = dataset.id
    await session.commit()
    return dataset_id, row_ids


def _build_response(
    req: DataSetImportViaPathRequest,
    rows: list[DataSetRowSchema],
    dataset_id: object,
    row_ids: list[object],
) -> DataSetImportViaPathResponse:
    """Build the API response from persisted IDs. Pure function — fully unit-testable."""
    return DataSetImportViaPathResponse(
        path=req.path,
        dataset=DataSetInfo(name=req.dataset_name, id=dataset_id),
        loaded=DataRowInfo(n=len(rows), ids=row_ids),
    )


def _check_file_exists(path: str) -> None:
    """Raise 404 if the file does not exist at the given path."""
    if not Path(path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )


def _build_dataset_model(name: str, rows: list[DataSetRowSchema]) -> DatasetModel:
    """Build a DatasetModel with its rows from validated schema objects.

    Pure function — no DB or HTTP dependencies, fully unit-testable.
    """
    return DatasetModel(
        name=name,
        rows=[
            DatasetRowModel(
                input=row.prompt,
                expected_output=row.expected_output,
                model_output=row.model_output,
            )
            for row in rows
        ],
    )


def _raise_if_errors(errors: list[tuple[int, str, list]]) -> None:
    """Raise 422 with structured error details if any lines failed validation."""
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[
                {
                    "line": i,
                    "content": line.strip(),
                    "errors": errs,
                }
                for i, line, errs in errors
            ],
        )


def _parse_and_validate_rows(file: TextIOWrapper) \
        -> tuple[list[DataSetRowSchema], list[tuple[int, str, list]]]:
    """Read a .jsonl file line by line, validating each line against DataSetJsonStructure.

    Returns a tuple of:
    - valid rows as DataSetJsonStructure instances
    - errors as (line_number, raw_line, pydantic_errors) for each invalid line

    Blank lines are silently skipped — common at the end of .jsonl files.
    Does not raise — the caller decides what to do with errors.
    """
    errors: list[tuple[int, str, list]] = []
    rows: list[DataSetRowSchema] = []
    for i, line in enumerate(file):
        # Skip blank lines (e.g. trailing newline at end of file).
        if not line.strip():
            continue
        try:
            rows.append(
                DataSetRowSchema.model_validate_json(line)
            )
        except ValidationError as e:
            # Collect the line number, raw content, and Pydantic error details.
            errors.append((i, line, e.errors()))
    return rows, errors
