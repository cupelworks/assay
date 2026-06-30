from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.expression import select

from assay.models import TestModel
from assay.schemas import CreateTestCaseResponse, PaginatedTestCases


async def get_all_created_tests(
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestCases:
    """Return a paginated list of all test cases with their assigned test type names.

    Args:
        session: Active async database session.
        offset: Number of records to skip (for pagination).
        limit: Maximum number of records to return.

    Returns:
        PaginatedTestCases with the current page of test cases, the total count of all
        tests in the database, and the offset and limit used for the query.
        Returns an empty item list if no tests exist.
    """
    # selectinload eagerly fetches test_type_assignments in a second query
    test_models = list((await session.scalars(
        select(TestModel).offset(offset).limit(limit)
        .options(selectinload(TestModel.test_type_assignments))
    )).all())

    # COUNT never returns NULL in practice, but session.scalar() is typed as T | None —
    # the `or 0` guards against that and satisfies the type checker.
    total = await session.scalar(select(func.count(TestModel.id))) or 0

    return PaginatedTestCases(
        test_cases=[
            CreateTestCaseResponse(
                id=test.id,
                name=test.name,
                input=test.input,
                model_output=test.model_output,
                expected_output=test.expected_output,
                test_type_names=[
                    test_type.test_type_name
                    for test_type in test.test_type_assignments
                ],
            )
            for test in test_models
        ],
        offset=offset,
        limit=limit,
        total=total,
    )
