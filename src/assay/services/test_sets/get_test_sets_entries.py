import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestSetEntryModel
from assay.schemas import PaginatedTestSetEntriesDetails, TestCaseID, TestSetEntryDetails
from assay.services.test_sets._common import _find_test_set_or_404


async def get_test_sets_linked_tests(
        test_set_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestSetEntriesDetails:
    """Orchestrates test set entry listing: validates the set exists, counts total
    entries, fetches the requested page, and returns a paginated response.

    Args:
        test_set_id: The UUID of the test set whose entries are being listed.
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with test set entry details, total count, offset, and limit.

    Raises:
        HTTPException 404: No test set exists with the given ID.
    """
    await _find_test_set_or_404(test_set_id, session)

    total = await session.scalar(select(func.count(TestSetEntryModel.test_id))
                                 .where(TestSetEntryModel.test_set_id == test_set_id)) or 0

    # `name` is not unique, so it alone can't guarantee a stable row order across
    # pages. `id` is the primary key and therefore always unique, so appending it
    # as a tiebreaker makes the ordering — and the pagination — fully deterministic.
    found = (
        await session.scalars(
            select(TestSetEntryModel)
            .where(TestSetEntryModel.test_set_id == test_set_id)
            .order_by(TestSetEntryModel.name, TestSetEntryModel.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()

    return PaginatedTestSetEntriesDetails(
        offset=offset,
        limit=limit,
        total=total,
        items=[
            TestSetEntryDetails(
                id=test.id,
                # test_id is the FK back to the live TestModel this entry was
                # snapshotted from — not this entry's own id.
                test_case_id=TestCaseID(id=test.test_id),
                name=test.name,
                input=test.input,
                expected_output=test.expected_output,
                model_output=test.model_output,
                test_type_names=[test_type for test_type in test.test_type_names]
            )
            for test in found
        ]
    )