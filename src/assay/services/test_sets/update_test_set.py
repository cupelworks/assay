import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.schemas import ModifyTestSetMetadataRequest, TestSetMetadata, TestSetName
from assay.services.test_sets._common import (
    _check_unique_test_set_name_or_409,
    _find_test_set_or_404,
)


async def update_test_set_metadata_by_id(
        test_set_id: uuid.UUID,
        request: ModifyTestSetMetadataRequest,
        session: AsyncSession,
) -> TestSetMetadata:
    """Orchestrates test set rename: fetches, validates uniqueness, persists, and
    returns the updated metadata.

    The uniqueness check only runs when the requested name actually differs from
    the test set's current name, so re-submitting the set's own unchanged name is
    a safe no-op rather than a false 409.

    Args:
        test_set_id: UUID of the test set to update.
        request: Request containing the new name (omit or set to `null` to leave
            it unchanged).
        session: Active async database session.

    Returns:
        The updated test set metadata (id, name, created_at).

    Raises:
        HTTPException: 404 if no test set with the given ID exists.
        HTTPException: 409 if another test set already has the requested name.
    """
    found = await _find_test_set_or_404(test_set_id, session)
    
    if request.name != found.name and request.name is not None:
        await _check_unique_test_set_name_or_409(
            TestSetName(name=request.name), session
        )
        found.name = request.name

    await session.commit()

    return TestSetMetadata(
        id=found.id,
        name=found.name,
        created_at=found.created_at,
    )
