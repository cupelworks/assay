from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestModel
from assay.schemas import CreateTestCaseRequest, CreateTestCaseResponse


async def create_new_test(
        request: CreateTestCaseRequest,
        session: AsyncSession) -> CreateTestCaseResponse:
    """Orchestrates test case creation: persists the model and returns the result.

    Args:
        request: Request containing the test name, input, and optional expected/model outputs.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The created test case with its generated ID and all input fields.
    """
    test = TestModel(
        name=request.name,
        input=request.input,
        model_output=request.model_output,
        expected_output=request.expected_output,
    )

    session.add(test)
    await session.commit()

    return CreateTestCaseResponse(
        id=test.id,
        name=test.name,
        input=test.input,
        model_output=test.model_output,
        expected_output=test.expected_output,
    )
