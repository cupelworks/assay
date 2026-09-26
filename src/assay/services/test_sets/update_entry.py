import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.schemas import ModifyTestCaseRequest, TestCaseID, TestSetEntryDetails, TestTypeAssignment
from assay.services.test_sets._common import (
    _check_test_set_entry_has_no_runs_or_409,
    _find_test_set_entry_in_specific_test_set_or_404,
    _find_test_set_or_404,
)
from assay.services.tests._common import (
    _check_reference_required_types_have_expected_output_or_422,
    _validate_test_type_assignments,
)
from assay.services.tests.update_test import _apply_scalar_updates

logger = logging.getLogger(__name__)


async def modify_entry_by_id(
        test_set_id: uuid.UUID,
        entry_id: uuid.UUID,
        request: ModifyTestCaseRequest,
        session: AsyncSession
) -> TestSetEntryDetails:
    """Partially update a test set entry and return the full updated record.

    Entries are editable only until they are first executed — once a run
    references the entry, it is frozen and a 409 is returned, so the run's
    record of what it executed against stays accurate. Note that editing an
    entry means it no longer reflects the originating test's state at
    snapshot time; test_case_id keeps pointing at the live test regardless.

    Only fields explicitly set in the request are written — omitted fields (None)
    are left unchanged. For test_type_assignments specifically: None leaves the
    snapshot list untouched, while [] clears it.

    Args:
        test_set_id: UUID of the test set the entry belongs to.
        entry_id: UUID of the entry to update.
        request: Partial update payload — any combination of name, input,
            expected_output, model_output, and test_type_assignments.
        session: Active async database session.

    Returns:
        The full updated entry, so the caller does not need a follow-up GET.

    Raises:
        HTTPException: 404 if the test set does not exist, or no entry with that
            ID exists in it.
        HTTPException: 409 if the entry has already been executed at least once.
        HTTPException: 422 if any provided test type name is unknown, a
            required config field is missing, or the *effective* state
            after this update would leave a reference-requiring type
            assigned with no expected_output (e.g. clearing
            expected_output while Exact Match stays assigned, untouched,
            from before this request).
    """
    await _find_test_set_or_404(test_set_id, session)
    found = await _find_test_set_entry_in_specific_test_set_or_404(test_set_id, entry_id, session)
    await _check_test_set_entry_has_no_runs_or_409(entry_id, session)

    # Computed from the pre-mutation state, before _apply_scalar_updates
    # touches `found` — see update_test.py's modify_test_by_id for why.
    effective_expected_output = (
        request.expected_output if request.expected_output is not None
        else found.expected_output
    )
    effective_assignments = (
        request.test_type_assignments if request.test_type_assignments is not None
        else [TestTypeAssignment(**item) for item in found.test_type_assignments]
    )
    await _check_reference_required_types_have_expected_output_or_422(
        session, effective_assignments, effective_expected_output
    )

    _apply_scalar_updates(found, request)

    if request.test_type_assignments is not None:
        await _validate_test_type_assignments(session, request.test_type_assignments)
        found.test_type_assignments = [
            {"name": assignment.name, "config": assignment.config}
            for assignment in request.test_type_assignments
        ]

    await session.commit()

    changed_fields = sorted(
        field for field in request.model_fields_set if getattr(request, field) is not None
    )
    logger.info(
        "Updated entry %s in test set %s (fields: %s)",
        entry_id, test_set_id, ", ".join(changed_fields),
        extra={"test_set_id": test_set_id, "entry_id": entry_id, "fields": changed_fields},
    )
    return TestSetEntryDetails(
        id=found.id,
        test_case_id=TestCaseID(id=found.test_id),
        name=found.name,
        input=found.input,
        expected_output=found.expected_output,
        model_output=found.model_output,
        test_type_assignments=found.test_type_assignments,
    )
