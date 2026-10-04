import uuid

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from assay.models import TestSetEntryModel, TestSetModel
from assay.schemas import PaginatedTestSetMetadataResponse, TestSetMetadata
from assay.services.test_sets._common import _find_test_set_or_404


async def get_all_test_sets_metadata(
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestSetMetadataResponse:
    """Orchestrates test set listing: counts total records, fetches the requested page,
    and returns a paginated response.

    Args:
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with test set metadata, total count, offset, and limit.
    """
    total = await session.scalar(select(func.count(TestSetModel.id))) or 0
    test_sets = (await session.scalars(
        select(TestSetModel).offset(offset).limit(limit)
    )).all()

    entry_counts = dict(
        (await session.execute(
            select(TestSetEntryModel.test_set_id, func.count(TestSetEntryModel.id))
            .where(TestSetEntryModel.test_set_id.in_([test_set.id for test_set in test_sets]))
            .group_by(TestSetEntryModel.test_set_id)
        )).tuples().all()
    )

    return PaginatedTestSetMetadataResponse(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            TestSetMetadata(
                id=test_set.id,
                name=test_set.name,
                created_at=test_set.created_at,
                entry_count=entry_counts.get(test_set.id, 0),
            )
            for test_set in test_sets
        ]
    )


async def get_test_set_metadata_by_id(
        test_set_id: uuid.UUID,
        session: AsyncSession,
) -> TestSetMetadata:
    """Orchestrates single test set retrieval: validates ID and returns its metadata.

    Args:
        test_set_id: The UUID of the test set to retrieve.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The test set metadata (id, name, created_at, entry_count).

    Raises:
        HTTPException 404: No test set exists with the given ID.
    """
    test_set = await _find_test_set_or_404(test_set_id, session)

    entry_count = await session.scalar(
        select(func.count(TestSetEntryModel.id))
        .where(TestSetEntryModel.test_set_id == test_set_id)
    ) or 0

    return TestSetMetadata(
        id=test_set.id,
        name=test_set.name,
        created_at=test_set.created_at,
        entry_count=entry_count,
    )
