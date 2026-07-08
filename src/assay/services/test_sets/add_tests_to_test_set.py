import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestSetEntryModel
from assay.schemas import TestCaseID, TestSetEntryID
from assay.services.test_sets._common import (
    _check_tests_not_in_test_set_or_409,
    _find_test_set_or_404,
)
from assay.services.tests._common import _find_all_tests_with_details_or_404


async def add_tests_to_test_set_by_test_id(
        test_set_id: uuid.UUID,
        request: list[TestCaseID],
        session: AsyncSession,
) -> list[TestSetEntryID]:
    """Snapshot the given tests into a test set, creating one entry per test.

    Runs three guards before writing:
    1. The test set must exist (404 otherwise).
    2. All requested test IDs must exist (404 otherwise).
    3. None of the requested tests may already be in the set (409 otherwise).

    Each snapshot copies name, input, expected_output, model_output, and test_type_names
    from the live test at the moment of this call. Subsequent edits to the originating
    test have no effect on the entry, though the entry itself can still be edited
    directly until it has been run at least once, after which it freezes. Duplicate
    test IDs in the request are silently deduplicated by the SQL IN clause — each test
    produces exactly one entry.

    Args:
        test_set_id: UUID of the test set to add entries to.
        request: List of test case IDs to snapshot into the set.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        A list of TestSetEntryID objects, one per created snapshot entry.

    Raises:
        HTTPException 404: The test set does not exist, or one or more test IDs were not found.
        HTTPException 409: One or more tests are already present in the test set.
    """
    await _find_test_set_or_404(test_set_id, session)

    test_ids = [test.id for test in request]
    found_tests = await _find_all_tests_with_details_or_404(test_ids, session)
    
    await _check_tests_not_in_test_set_or_409(test_set_id, test_ids, session)

    test_set_entries_model = [
        TestSetEntryModel(
            id=uuid.uuid4(),
            test_set_id=test_set_id,
            test_id=test.id,
            name=test.name,
            input=test.input,
            expected_output=test.expected_output,
            model_output=test.model_output,
            test_type_names=[test_type.test_type_name for test_type in test.test_type_assignments],
            snapshot_at=datetime.now(),
        )
        for test in found_tests
    ]

    test_set_entries_ids = [
        TestSetEntryID(
            id=test_set_entry.id,
        )
        for test_set_entry in test_set_entries_model
    ]

    session.add_all(test_set_entries_model)
    await session.commit()

    return test_set_entries_ids
