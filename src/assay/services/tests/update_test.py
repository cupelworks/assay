import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestTypeAssignmentModel
from assay.schemas import CreateTestCaseResponse, ModifyTestCaseRequest
from assay.services.tests._common import _find_test_by_id_or_404, _validate_test_type_name


async def modify_test_by_id(
        test_case_id: uuid.UUID,
        request: ModifyTestCaseRequest,
        session: AsyncSession,
) -> CreateTestCaseResponse:
    """Partially update a test case and return the full updated record.

    Only fields explicitly set in the request are written — omitted fields (None) are left
    unchanged. For test_type_names specifically: None leaves assignments untouched,
    while [] removes all existing assignments.

    Args:
        test_case_id: UUID of the test case to update.
        request: Partial update payload — any combination of name, input, expected_output,
            model_output, and test_type_names.
        session: Active async database session.

    Returns:
        The full updated test case, so the caller does not need a follow-up GET.

    Raises:
        HTTPException: 404 if no test case with the given ID exists.
        HTTPException: 422 if any provided test type name is not in the catalogue.
    """
    found = await _find_test_by_id_or_404(test_case_id, session)

    if request.name is not None:
        found.name = request.name
    if request.input is not None:
        found.input = request.input
    if request.expected_output is not None:
        found.expected_output = request.expected_output
    if request.model_output is not None:
        found.model_output = request.model_output

    # None means "don't touch assignments"; [] means "remove all"
    if request.test_type_names is not None:
        await _validate_test_type_name(session, request.test_type_names)
        found.test_type_assignments = [
            TestTypeAssignmentModel(test_id=found.id, test_type_name=name)
            for name in request.test_type_names
        ]

    await session.commit()

    return CreateTestCaseResponse(
        id=found.id,
        name=found.name,
        input=found.input,
        model_output=found.model_output,
        expected_output=found.expected_output,
        test_type_names=[a.test_type_name for a in found.test_type_assignments],
    )
