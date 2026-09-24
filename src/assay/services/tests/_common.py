import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status

from assay.models import ConfigFieldKind, TestModel, TestTypesModel
from assay.schemas import TestTypeAssignment


def _check_difference_between_found_tests_and_requested_tests(
        test_ids: list[uuid.UUID],
        found_test_ids: list[uuid.UUID],
) -> None:
    """Raise 404 if any requested test ID is absent from the found results.

    Args:
        test_ids: The IDs originally requested by the caller.
        found_test_ids: The IDs actually returned by the database query.

    Raises:
        HTTPException: 404 listing the IDs present in test_ids but missing from found_test_ids.
    """
    # set difference identifies which requested IDs are missing from the DB
    difference = set(test_ids) - set(found_test_ids)

    if difference:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tests with ids {[str(test_id) for test_id in difference]} not found",
        )


async def _find_all_tests_or_404(
        test_ids: list[uuid.UUID],
        session: AsyncSession) -> None:
    """Validate that all given test IDs exist, raising 404 if any are missing.

    Only fetches IDs from the database — use _find_all_tests_with_details_or_404
    when full model instances are needed.

    Args:
        test_ids: List of test UUIDs to look up.
        session: Active async database session.

    Raises:
        HTTPException: 404 listing the IDs that were not found.
    """
    # select only the id column — no need to load full model instances
    found_test_ids = list((await session.scalars(
        select(TestModel.id)
        .where(TestModel.id.in_(test_ids))
    )).all())

    _check_difference_between_found_tests_and_requested_tests(test_ids, found_test_ids)


async def _find_all_tests_with_details_or_404(
        test_ids: list[uuid.UUID],
        session: AsyncSession,
):
    """Fetch full TestModel instances for the given IDs, raising 404 if any are missing.

    Args:
        test_ids: List of test UUIDs to look up.
        session: Active async database session.

    Returns:
        A list of TestModel instances matching the given IDs.

    Raises:
        HTTPException: 404 listing the IDs that were not found.
    """
    found_test = (await session.scalars(
        select(TestModel)
        .where(TestModel.id.in_(test_ids))
        .options(selectinload(TestModel.test_type_assignments))
    )).all()

    _check_difference_between_found_tests_and_requested_tests(
        test_ids, [test.id for test in found_test]
    )

    return found_test


async def _find_test_by_id_or_404(
        test_id: uuid.UUID,
        session: AsyncSession
):
    """Fetch a single test case by ID, eagerly loading its type assignments, or raise 404.

    Args:
        test_id: UUID of the test case to fetch.
        session: Active async database session.

    Returns:
        The matching TestModel with test_type_assignments already loaded.

    Raises:
        HTTPException: 404 if no test with the given ID exists.
    """
    found = await session.scalar(
        select(TestModel).where(TestModel.id == test_id)
        .options(selectinload(TestModel.test_type_assignments))
    )
    
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Test with id {str(test_id)} not found",
        )

    return found


async def _validate_test_type_assignments(
        session: AsyncSession,
        assignments: list[TestTypeAssignment]) -> None:
    """Checks that every assigned test type exists in the catalogue and that
    each assignment supplies a value for every required config field its
    type declares.

    A config_fields entry of kind "reference" is never checked here — it
    resolves from the test case's own expected_output, not from an
    assignment's config (see docs/test_type_config/dev_notes.md).

    Args:
        session: Async SQLAlchemy session.
        assignments: Test type assignments to validate — each carries the
            type's name and any per-field config values supplied for it.

    Raises:
        HTTPException: 422 listing unknown test type names and/or
            assignments missing a required (non-reference) config field,
            if either problem is found. Both are reported together in one
            exception rather than failing on whichever is found first.
    """
    requested_names = [assignment.name for assignment in assignments]

    found = (await session.execute(
        select(TestTypesModel.name, TestTypesModel.config_fields)
        .where(TestTypesModel.name.in_(requested_names))
    )).all()
    config_fields_by_name = {row.name: row.config_fields for row in found}

    unknown_names = set(requested_names) - set(config_fields_by_name)

    missing_required_fields = []
    for assignment in assignments:
        config_fields = config_fields_by_name.get(assignment.name)
        if config_fields is None:
            continue  # already reported via unknown_names

        config = assignment.config or {}
        for field in config_fields:
            if field["kind"] == ConfigFieldKind.reference or not field["required"]:
                continue
            if not config.get(field["key"]):
                missing_required_fields.append(
                    f"'{assignment.name}' is missing required config field '{field['key']}'"
                )

    if not unknown_names and not missing_required_fields:
        return

    problems = []
    if unknown_names:
        problems.append(f"Unknown test types: {sorted(unknown_names)}")
    problems.extend(missing_required_fields)

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="; ".join(problems),
    )
