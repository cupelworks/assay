import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestModel, TestSetEntryModel, TestTypeAssignmentModel
from assay.schemas import CreateTestCaseResponse, ModifyTestCaseRequest, TestTypeAssignment
from assay.services.tests._common import (
    _check_reference_required_types_have_expected_output_or_422,
    _find_test_by_id_or_404,
    _validate_test_type_assignments,
)


def _apply_scalar_updates(
        found: TestModel | TestSetEntryModel,
        request: ModifyTestCaseRequest
) -> None:
    """Write each explicitly-set scalar field from the request onto the test case.

    Omitted fields (None) are left unchanged.

    Args:
        found: The test case being updated.
        request: Partial update payload.
    """
    if request.name is not None:
        found.name = request.name
    if request.input is not None:
        found.input = request.input
    if request.expected_output is not None:
        found.expected_output = request.expected_output
    if request.model_output is not None:
        found.model_output = request.model_output


async def _apply_test_type_assignments_update(
        found: TestModel,
        request: ModifyTestCaseRequest,
        session: AsyncSession,
) -> None:
    """Validate and rebuild the test case's test type assignments, if requested.

    None means "don't touch assignments"; [] means "remove all".

    Args:
        found: The test case being updated.
        request: Partial update payload.
        session: Active async database session.

    Raises:
        HTTPException: 422 if any provided test type name is unknown or a
            required config field is missing.
    """
    if request.test_type_assignments is not None:
        await _validate_test_type_assignments(session, request.test_type_assignments)
        found.test_type_assignments = [
            TestTypeAssignmentModel(
                test_id=found.id,
                test_type_name=assignment.name,
                config=assignment.config,
            )
            for assignment in request.test_type_assignments
        ]


async def modify_test_by_id(
        test_case_id: uuid.UUID,
        request: ModifyTestCaseRequest,
        session: AsyncSession,
) -> CreateTestCaseResponse:
    """Partially update a test case and return the full updated record.

    Only fields explicitly set in the request are written — omitted fields (None) are left
    unchanged. For test_type_assignments specifically: None leaves assignments untouched,
    while [] removes all existing assignments.

    Args:
        test_case_id: UUID of the test case to update.
        request: Partial update payload — any combination of name, input, expected_output,
            model_output, and test_type_assignments.
        session: Active async database session.

    Returns:
        The full updated test case, so the caller does not need a follow-up GET.

    Raises:
        HTTPException: 404 if no test case with the given ID exists.
        HTTPException: 422 if any provided test type name is unknown, a
            required config field is missing, or the *effective* state
            after this update — the new value if this request changes it,
            the existing one otherwise — would leave a reference-requiring
            type assigned with no expected_output (e.g. clearing
            expected_output while Exact Match stays assigned, untouched,
            from before this request).
    """
    found = await _find_test_by_id_or_404(test_case_id, session)

    # Computed from the pre-mutation state, before _apply_scalar_updates /
    # _apply_test_type_assignments_update touch `found` — this is what the
    # test will look like once this request is applied, whether or not
    # this particular request is the one changing either field.
    effective_expected_output = (
        request.expected_output if request.expected_output is not None
        else found.expected_output
    )
    effective_assignments = (
        request.test_type_assignments if request.test_type_assignments is not None
        else [
            TestTypeAssignment(name=a.test_type_name, config=a.config)
            for a in found.test_type_assignments
        ]
    )
    await _check_reference_required_types_have_expected_output_or_422(
        session, effective_assignments, effective_expected_output
    )

    _apply_scalar_updates(found, request)
    await _apply_test_type_assignments_update(found, request, session)

    await session.commit()

    return CreateTestCaseResponse(
        id=found.id,
        name=found.name,
        input=found.input,
        model_output=found.model_output,
        expected_output=found.expected_output,
        test_type_assignments=[
            TestTypeAssignment(name=a.test_type_name, config=a.config)
            for a in found.test_type_assignments
        ],
    )
