from io import TextIOWrapper
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from assay.models import DatasetModel, DatasetRowModel
from assay.schemas import (
    DataRowInfo,
    DataSetImportViaPathRequest,
    DataSetImportViaPathResponse,
    DataSetInfo,
    DataSetJsonStructure,
)


async def upload_dataset_via_path(req: DataSetImportViaPathRequest,
                                  session: AsyncSession) -> DataSetImportViaPathResponse:
    """Load a .jsonl dataset from a local path and persist it as a Dataset with its rows.

    Validates all lines before inserting anything — if any line fails schema validation
    the whole file is rejected and nothing is written (fail fast, no partial imports).
    On success, creates one DatasetModel and one DatasetRowModel per line within the same
    transaction. IDs are captured after flush (before commit) to avoid async lazy-load issues.
    If the commit fails, the transaction is rolled back and nothing is persisted.

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
    # Fail early if the file doesn't exist — no point opening it.
    if not Path(req.path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {req.path}"
        )

    # Check name uniqueness before any file I/O — cheapest checks first.
    # scalar() returns the DatasetModel instance if found, None otherwise.
    dataset_name_existing = await session.scalar(
        select(DatasetModel).where(DatasetModel.name == req.dataset_name)
    )
    if dataset_name_existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset name '{req.dataset_name}' already exists.",
        )

    # File is closed immediately after parsing — DB work happens on in-memory objects.
    with open(req.path) as f:
        rows, errors = _parse_and_validate_rows(f)

    # Fail fast — reject the whole file if any line is invalid, no partial inserts.
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

    # Build the dataset and its rows in one shot — SQLAlchemy handles the FK linkage.
    dataset = DatasetModel(
        name=req.dataset_name,
        rows=[
            DatasetRowModel(
                input=row.prompt,
                expected_output=row.expected_output,
                model_output=row.model_output,
            )
            for row in rows
        ]
    )

    session.add(dataset)
    # flush() sends INSERT and populates DB-generated IDs without committing.
    # IDs are captured while objects are still live — avoids lazy-load issues after commit.
    await session.flush()
    row_ids = [r.id for r in dataset.rows]
    dataset_id = dataset.id

    # Transaction is still open — if commit fails, everything is rolled back.
    await session.commit()

    return DataSetImportViaPathResponse(
        path=req.path,
        dataset=DataSetInfo(name=req.dataset_name, id=dataset_id),
        loaded=DataRowInfo(n=len(rows), ids=row_ids),
    )

def _parse_and_validate_rows(file: TextIOWrapper) \
        -> tuple[list[DataSetJsonStructure], list[tuple[int, str, list]]]:
    """Read a .jsonl file line by line, validating each line against DataSetJsonStructure.

    Returns a tuple of:
    - valid rows as DataSetJsonStructure instances
    - errors as (line_number, raw_line, pydantic_errors) for each invalid line

    Blank lines are silently skipped — common at the end of .jsonl files.
    Does not raise — the caller decides what to do with errors.
    """
    errors: list[tuple[int, str, list]] = []
    rows: list[DataSetJsonStructure] = []
    for i, line in enumerate(file):
        # Skip blank lines (e.g. trailing newline at end of file).
        if not line.strip():
            continue
        try:
            rows.append(
                DataSetJsonStructure.model_validate_json(line)
            )
        except ValidationError as e:
            # Collect the line number, raw content, and Pydantic error details.
            errors.append((i, line, e.errors()))
    return rows, errors
