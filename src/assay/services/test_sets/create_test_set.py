import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestSetModel
from assay.schemas import TestSetCreationResponse, TestSetName
from assay.services.test_sets._common import _check_unique_test_set_name_or_409


async def create_new_test_set(
        request: TestSetName,
        session: AsyncSession) -> TestSetCreationResponse:
    """Create a new test set with a unique name.

    Args:
        request: Request containing the test set name.
        session: Active async database session.

    Returns:
        The created test set with its generated ID and name.

    Raises:
        HTTPException: 409 if a test set with the given name already exists.
    """
    await _check_unique_test_set_name_or_409(request, session)

    new_test_id = uuid.uuid4()
    test_set_model = TestSetModel(
        id=new_test_id,
        name=request.name,
    )

    session.add(test_set_model)
    await session.commit()

    return TestSetCreationResponse(
        name=request.name,
        id=new_test_id,
    )
