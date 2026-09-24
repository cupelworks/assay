from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestTypes, TestTypesModel
from assay.schemas import TestTypesSchema


async def get_test_types_by_category(
        category: TestTypes,
        session: AsyncSession,
) -> list[TestTypesSchema]:
    """Return every catalogue entry in a given test type category.

    Args:
        category: The test type category to filter by (deterministic, nlp_metric,
            or llm_as_judge).
        session: Active async database session.

    Returns:
        All TestTypesModel rows matching the category, ordered by name (descending),
        as TestTypesSchema. Returns an empty list if no test types exist in that
        category.
    """
    found = await session.scalars(
        select(TestTypesModel)
        .where(TestTypesModel.category == category)
        .order_by(TestTypesModel.name.desc(), TestTypesModel.id.desc())
    )

    return [
        TestTypesSchema(
            id=test_type.id,
            name=test_type.name,
            category=test_type.category,
            description=test_type.description,
            is_active=test_type.is_active,
            created_at=test_type.created_at,
            best_for=test_type.best_for,
            cost=test_type.cost,
            limitations=test_type.limitations,
            config_fields=test_type.config_fields,
        )
        for test_type in found
    ]
