import uuid

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from assay.models import DatasetRowModel
from assay.schemas import PaginatedDataSetRowResponse
from assay.services._listing import contains_text
from assay.services.datasets._common import _describe_rows, _get_dataset_or_404


async def get_dataset_rows_by_id(dataset_id: uuid.UUID, session: AsyncSession, offset: int,
                                 limit: int, q: list[str] | None = None
                                 ) -> PaginatedDataSetRowResponse:
    """A dataset's rows by number, within the search phrases `q` (each found in
    the prompt, expected output or model output), paged; each with its number
    and how many tests were made from it.

    Raises:
        HTTPException 404: No dataset exists with the given ID.
    """
    await _get_dataset_or_404(dataset_id, session)
    where = [DatasetRowModel.dataset_id == dataset_id,
             *(contains_text(phrase.strip(), DatasetRowModel.input,
                             DatasetRowModel.expected_output, DatasetRowModel.model_output)
               for phrase in q or [] if phrase.strip())]
    total = await session.scalar(select(func.count(DatasetRowModel.id)).where(*where)) or 0
    rows = await session.scalars(select(DatasetRowModel).where(*where)
                                 .order_by(DatasetRowModel.position)
                                 .offset(offset).limit(limit))
    return PaginatedDataSetRowResponse(id=dataset_id,
                                       items=await _describe_rows(list(rows), session),
                                       total=total, offset=offset, limit=limit)
