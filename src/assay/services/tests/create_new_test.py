import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from assay.models import TestModel, TestTypeAssignmentModel, TestTypesModel
from assay.schemas import CreateTestCaseRequest, CreateTestCaseResponse


async def create_new_test(
        request: CreateTestCaseRequest,
        session: AsyncSession) -> CreateTestCaseResponse:
    """Orchestrates test case creation: persists the model and returns the result.

    Validates test type names against the catalogue before inserting. Raises
    HTTP 422 if any name is not found in test_types.

    Args:
        request: Request containing the test name, input, optional expected/model outputs,
            and optional list of test type names to assign.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The created test case with its generated ID and all input fields.
    """
    if request.test_type_names:
        await _validate_test_type_name(session, request.test_type_names)

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
            test_type_name=test_type_name
        )
        for test_type_name in request.test_type_names
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
        test_type_names=request.test_type_names,
    )


async def _validate_test_type_name(
        session: AsyncSession,
        test_type_names: list[str]) -> None:
    """Checks that all requested test type names exist in the test_types catalogue.

    Args:
        session: Async SQLAlchemy session.
        test_type_names: Names to validate against the catalogue.

    Raises:
        HTTPException: 422 if any name is not found in test_types.
    """
    found = await session.scalars(
        select(TestTypesModel.name)
        .where(TestTypesModel.name.in_(test_type_names))
    )

    difference_set = set(test_type_names) - set(found.all())
    if difference_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown test types: {difference_set}"
        )
