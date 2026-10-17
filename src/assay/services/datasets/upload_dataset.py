# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""A dataset made from an uploaded `.jsonl` file: every line a valid row, or
nothing is created."""
import logging
import re

from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from assay.config import settings
from assay.models import DatasetModel
from assay.schemas import (
    DataRowInfo,
    DataSetImportedData,
    DataSetInfo,
    DataSetInvalidLines,
    DataSetLineError,
    DataSetLineProblem,
    DataSetRowSchema,
    DataSetUploadLimits,
)
from assay.services.datasets._common import _check_name_unique, _new_rows

logger = logging.getLogger(__name__)

# The invalid lines a 422 lists; it counts all of them.
LISTED_LINES = 100
_CHUNK_BYTES = 1024 * 1024
# A line ends as an editor ends it: \n, \r\n or a lone \r.
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_COLUMN = re.compile(r"column (\d+)")


def upload_limits() -> DataSetUploadLimits:
    """The largest file an upload accepts."""
    return DataSetUploadLimits(max_bytes=_max_bytes())


async def upload_dataset(file: UploadFile, dataset_name: str,
                         session: AsyncSession) -> DataSetImportedData:
    """Create a dataset from an uploaded file, one row per non-blank line.

    Raises:
        HTTPException 409: A dataset with the same name already exists (checked
            before the file is read).
        HTTPException 413: The file is larger than the limit.
        HTTPException 422: The file isn't UTF-8 text, or a line isn't a valid row.
    """
    await _check_name_unique(dataset_name, session)
    text = _decoded(await _read_within_limit(file, _max_bytes()))
    rows, invalid_count, listed = _parse_lines(text)
    if invalid_count:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=_invalid_lines(invalid_count, listed).model_dump())

    dataset = DatasetModel(name=dataset_name, rows=_new_rows(rows))
    session.add(dataset)
    await session.flush()
    row_ids = [row.id for row in dataset.rows]
    dataset_id = dataset.id
    await session.commit()

    logger.info("Created dataset %s with %d rows from an upload", dataset_id, len(rows),
                extra={"dataset_id": dataset_id, "row_count": len(rows)})
    return DataSetImportedData(dataset=DataSetInfo(id=dataset_id, name=dataset_name),
                               loaded=DataRowInfo(n=len(rows), ids=row_ids))


def _max_bytes() -> int:
    return settings.dataset_upload_max_mb * 1024 * 1024


async def _read_within_limit(file: UploadFile, max_bytes: int) -> bytes:
    """The file's bytes, read in chunks; 413 as soon as they pass `max_bytes`."""
    content = bytearray()
    while chunk := await file.read(_CHUNK_BYTES):
        content += chunk
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"The file is larger than {settings.dataset_upload_max_mb} MB.",
            )
    return bytes(content)


def _decoded(content: bytes) -> str:
    """The file's text, read as UTF-8 with or without a byte-order mark."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="The file isn't UTF-8 text.") from None


def _parse_lines(text: str) -> tuple[list[DataSetRowSchema], int, list[DataSetLineError]]:
    """The valid rows, how many lines are invalid, and the first LISTED_LINES of
    them. Lines are numbered from 1, blank ones included, and blank ones are
    skipped."""
    rows: list[DataSetRowSchema] = []
    invalid_count = 0
    listed: list[DataSetLineError] = []
    for number, line in enumerate(_LINE_BREAK.split(text), start=1):
        if not line.strip():
            continue
        try:
            rows.append(DataSetRowSchema.model_validate_json(line))
        except ValidationError as error:
            invalid_count += 1
            if len(listed) < LISTED_LINES:
                listed.append(_line_error(number, error))
    return rows, invalid_count, listed


def _line_error(number: int, error: ValidationError) -> DataSetLineError:
    """A line's problems, without its content."""
    problems = [DataSetLineProblem(loc=list(found["loc"]), msg=found["msg"], type=found["type"])
                for found in error.errors(include_url=False, include_context=False,
                                          include_input=False)]
    return DataSetLineError(line=number, msg=_line_message(problems), errors=problems)


def _line_message(problems: list[DataSetLineProblem]) -> str:
    """What's wrong with a line, in plain words, every problem in turn."""
    return " ".join(_problem_message(problem) for problem in problems)


def _problem_message(problem: DataSetLineProblem) -> str:
    field = ".".join(str(part) for part in problem.loc)
    if problem.type == "json_invalid":
        column = _COLUMN.search(problem.msg)
        return f"Not valid JSON (near character {column.group(1)})." if column \
            else "Not valid JSON."
    if problem.type == "model_type" and not field:
        return "Not a JSON object."
    if problem.type == "missing":
        return f"{field} is missing."
    if problem.type == "string_type":
        return f"{field} must be text."
    return f"{field}: {problem.msg}." if field else f"{problem.msg}."


def _invalid_lines(count: int, listed: list[DataSetLineError]) -> DataSetInvalidLines:
    summary = ("1 line isn't a valid dataset row." if count == 1
               else f"{count} lines aren't valid dataset rows.")
    return DataSetInvalidLines(msg=f"{summary} Nothing was imported.", invalid_lines=count,
                               lines=listed)
