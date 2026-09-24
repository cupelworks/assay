import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestModel, TestTypeAssignmentModel
from assay.schemas import (
    CreateTestCaseFromDatasetRequest,
    CreateTestCaseFromDatasetResponse,
    CreateTestCaseRequest,
    CreateTestCaseResponse,
    TestCaseID,
)
from assay.services.datasets._common import _get_all_rows_or_404, _get_dataset_or_404
from assay.services.tests._common import _validate_test_type_assignments


async def create_new_test(
        request: CreateTestCaseRequest,
        session: AsyncSession) -> CreateTestCaseResponse:
    """Orchestrates test case creation: persists the model and returns the result.

    Validates test type assignments against the catalogue before inserting.
    Raises HTTP 422 if any name is unknown or a required config field is
    missing.

    Args:
        request: Request containing the test name, input, optional expected/model outputs,
            and optional list of test type assignments.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The created test case with its generated ID and all input fields.
    """
    if request.test_type_assignments:
        await _validate_test_type_assignments(session, request.test_type_assignments)

    test = TestModel(
        id=uuid.uuid4(),
        name=request.name,
        input=request.input,
        model_output=request.model_output,
        expected_output=request.expected_output,
    )

    test_types = [
        TestTypeAssignmentModel(
            test_id=test.id,
            test_type_name=assignment.name,
            config=assignment.config,
        )
        for assignment in request.test_type_assignments
    ]

    session.add(test)
    session.add_all(test_types)
    await session.commit()

    return CreateTestCaseResponse(
        id=test.id,
        name=test.name,
        input=test.input,
        model_output=test.model_output,
        expected_output=test.expected_output,
        test_type_assignments=request.test_type_assignments,
    )


async def create_new_test_from_dataset(
        request: CreateTestCaseFromDatasetRequest,
        session: AsyncSession) -> CreateTestCaseFromDatasetResponse:
    """Create test cases in bulk from all rows of an existing dataset.

    Args:
        request: Dataset ID and optional list of test type assignments.
        session: Active async database session.

    Returns:
        `CreateTestCaseFromDatasetResponse` with the dataset ID and the IDs of all created tests.

    Raises:
        HTTPException: 404 if the dataset or its rows are not found.
        HTTPException: 422 if any test type name is unknown or a required
            config field is missing.
    """
    await _get_dataset_or_404(request.id, session)
    rows = await _get_all_rows_or_404(request.id, session)

    if request.test_type_assignments:
        await _validate_test_type_assignments(session, request.test_type_assignments)

    tests = [
        TestModel(
            id=uuid.uuid4(),
            dataset_row_id=row.id,
            name=str(uuid.uuid4()),
            input=row.input,
            model_output=row.model_output,
            expected_output=row.expected_output,
        )
        for row in rows
    ]

    test_types = [
        TestTypeAssignmentModel(
            test_id=test.id,
            test_type_name=assignment.name,
            config=assignment.config,
        )
        for test in tests for assignment in request.test_type_assignments
    ]

    session.add_all(tests)
    session.add_all(test_types)
    await session.commit()

    return CreateTestCaseFromDatasetResponse(
        test_cases=[
            TestCaseID(
                id=test.id
            )
            for test in tests
        ],
        id=request.id
    )
