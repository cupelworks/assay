import json
import math
import uuid

import regex
from fastapi import HTTPException
from jsonpath_ng import parse as parse_jsonpath
from jsonschema import validators
from jsonschema.exceptions import SchemaError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status

from assay.models import ConfigFieldKind, DatasetRowModel, TestModel, TestTypesModel
from assay.schemas import TestTypeAssignment


def _frozen_test_type_assignments(test: TestModel) -> list[dict]:
    """The test's assigned types with their config, in the frozen JSON shape
    every snapshot of a test stores — a TestSetEntryModel when the test is
    added to a set, a StandaloneRunModel when a standalone run is created:
    `[{"name": ..., "config": ..., "answer_path": ...}]`. The test's assignments must be loaded.
    """
    return [
        {"name": assignment.test_type_name, "config": assignment.config,
         "answer_path": assignment.answer_path}
        for assignment in test.test_type_assignments
    ]


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
    """Checks that every assigned test type exists in the catalogue, that
    each assignment supplies a value for every required config field its
    type declares, and that every value given is valid for its field's
    kind — see _invalid_reason — as is an assignment's own answer_path.

    A broken value is refused here rather than left to fail when a run
    executes: there it would fail its check and turn the run Amber or Red,
    blaming the application for a typo in the test.

    A config_fields entry of kind "reference" is never checked here — it
    resolves from the test case's own expected_output, not from an
    assignment's config. `multiline` and `rubric` are free text, checked for
    presence only.

    Args:
        session: Async SQLAlchemy session.
        assignments: Test type assignments to validate — each carries the
            type's name and any per-field config values supplied for it.

    Raises:
        HTTPException: 422 listing unknown test type names, assignments
            missing a required (non-reference) config field, and values
            that aren't valid for their field, if any is found. All
            are reported together in one exception rather than failing on
            whichever is found first.
    """
    requested_names = [assignment.name for assignment in assignments]

    found = (await session.execute(
        select(TestTypesModel.name, TestTypesModel.config_fields)
        .where(TestTypesModel.name.in_(requested_names))
    )).all()
    config_fields_by_name = {row.name: row.config_fields for row in found}

    unknown_names = set(requested_names) - set(config_fields_by_name)

    field_problems = []
    for assignment in assignments:
        config_fields = config_fields_by_name.get(assignment.name)
        if config_fields is None:
            continue  # already reported via unknown_names

        if assignment.answer_path:
            reason = _invalid_reason({"kind": ConfigFieldKind.jsonpath},
                                     assignment.answer_path)
            if reason:
                field_problems.append(f"'{assignment.name}' answer_path {reason}")

        config = assignment.config or {}
        for field in config_fields:
            if field["kind"] == ConfigFieldKind.reference:
                continue
            value = config.get(field["key"])
            if not value:
                if field["required"]:
                    field_problems.append(
                        f"'{assignment.name}' is missing required config field '{field['key']}'"
                    )
                continue
            reason = _invalid_reason(field, value)
            if reason:
                field_problems.append(
                    f"'{assignment.name}' config field '{field['key']}' {reason}"
                )

    if not unknown_names and not field_problems:
        return

    problems = []
    if unknown_names:
        problems.append(f"Unknown test types: {sorted(unknown_names)}")
    problems.extend(field_problems)

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="; ".join(problems),
    )


def _invalid_reason(field: dict, value: str) -> str | None:
    """Why value isn't valid for field, or None when it is (or the field's
    kind is free text). The reason follows "'<type>' config field '<key>'".

    The checks use what the worker uses, so a value accepted here is one the
    engine can read: the `regex` library, not the stdlib `re`, whose syntax
    differs; `jsonschema`'s validator for the draft the schema's own $schema
    names.
    """
    match field["kind"]:
        case ConfigFieldKind.numeric:
            return _numeric_reason(field, value)
        case ConfigFieldKind.json:
            try:
                json.loads(value)
            except json.JSONDecodeError as exc:
                return f"is not valid JSON: {exc}"
        case ConfigFieldKind.jsonpath:
            try:
                parse_jsonpath(value)
            except Exception as exc:  # jsonpath-ng's parser raises assorted exception types
                return f"is not a valid JSONPath: {exc}"
        case ConfigFieldKind.regex:
            try:
                regex.compile(value)
            except regex.error as exc:
                return f"is not a valid regex pattern: {exc}"
        case ConfigFieldKind.json_schema:
            try:
                schema = json.loads(value)
            except json.JSONDecodeError as exc:
                return f"is not valid JSON: {exc}"
            if not isinstance(schema, dict | bool):
                return "is not a valid JSON Schema: it must be a JSON object or a boolean"
            try:
                validators.validator_for(schema).check_schema(schema)
            except SchemaError as exc:
                return f"is not a valid JSON Schema: {exc.message}"
    return None


def _numeric_reason(field: dict, value: str) -> str | None:
    """Why value isn't a number this numeric field accepts: it must parse,
    be finite, be whole when the field says `integer`, and lie within the
    field's `min`/`max`, both inclusive. Like every reason here, it names
    the rule, never the submitted value."""
    try:
        number = float(value)
    except ValueError:
        return "is not a number"
    if not math.isfinite(number):
        return "is not a number"
    if field.get("integer") and not number.is_integer():
        return "is not a whole number"
    low, high = field.get("min"), field.get("max")
    if (low is not None and number < low) or (high is not None and number > high):
        if low is not None and high is not None:
            return f"must be between {low:g} and {high:g}"
        if low is not None:
            return f"must be at least {low:g}"
        return f"must be at most {high:g}"
    return None


async def _find_test_type_names_needing_reference(
        session: AsyncSession,
        names: list[str],
) -> set[str]:
    """Return which of the given test type names have a required "reference"
    config field.

    A "reference"-kind field never appears in an assignment's own config —
    it always resolves to expected_output instead. This is the shared lookup behind
    _check_reference_required_types_have_expected_output_or_422, factored
    out so the dataset-import bulk path can reuse one catalogue query
    across every row instead of repeating it per row.

    Args:
        session: Active async database session.
        names: Test type names to check. Unknown names are silently
            ignored — callers are expected to have already validated
            existence via _validate_test_type_assignments.

    Returns:
        The subset of `names` whose catalogue entry has a required
        "reference" config field.
    """
    found = (await session.execute(
        select(TestTypesModel.name, TestTypesModel.config_fields)
        .where(TestTypesModel.name.in_(names))
    )).all()

    return {
        row.name for row in found
        if any(
            field["kind"] == ConfigFieldKind.reference and field["required"]
            for field in row.config_fields
        )
    }


async def _check_reference_required_types_have_expected_output_or_422(
        session: AsyncSession,
        assignments: list[TestTypeAssignment],
        expected_output: str | None,
) -> None:
    """Raise 422 if any assigned type needs a "reference" but expected_output
    is empty.

    Nothing else in the system catches this: a "reference"-kind config
    field is deliberately excluded from _validate_test_type_assignments'
    per-assignment check (it resolves from expected_output, not config),
    so without this guard a test/entry can be created or updated with e.g.
    Exact Match assigned and no expected_output at all — a run against it
    would sit pending forever with no way to ever produce a score, the
    exact failure mode every other required-field guard in this module
    already rejects upfront.

    Callers pass the *effective* expected_output and assignments the
    write will result in — not just what this one request happens to
    touch — so that e.g. clearing expected_output on a PATCH while
    leaving an already-assigned Exact Match untouched is caught too, not
    just the case where both change in the same request.

    Args:
        session: Active async database session.
        assignments: The effective test type assignments after this write.
        expected_output: The effective expected_output after this write.

    Raises:
        HTTPException: 422 listing every assigned type that needs a
            reference, if expected_output is empty.
    """
    if expected_output or not assignments:
        return

    needing_reference = await _find_test_type_names_needing_reference(
        session, [assignment.name for assignment in assignments]
    )

    if needing_reference:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Test types {sorted(needing_reference)} require a "
                   f"non-empty expected_output, but none was provided",
        )


async def _check_reference_required_types_have_expected_output_for_rows_or_422(
        session: AsyncSession,
        assignments: list[TestTypeAssignment],
        rows: list[DatasetRowModel],
) -> None:
    """Bulk variant of _check_reference_required_types_have_expected_output_or_422
    for dataset import.

    The same assignments apply to every row created from a dataset, but
    each row has its own expected_output — so unlike the single-item
    version, this has to check every row individually. Reports every
    offending row together in one 422 (all-or-nothing, no partial import),
    the same bulk-guard convention used throughout this codebase, rather
    than failing on the first bad row found.

    Args:
        session: Active async database session.
        assignments: The test type assignments shared by every row.
        rows: Dataset rows about to become tests, each with its own
            expected_output.

    Raises:
        HTTPException: 422 listing every assigned type that needs a
            reference and every row missing one, if any row's
            expected_output is empty while such a type is assigned.
    """
    if not assignments:
        return

    needing_reference = await _find_test_type_names_needing_reference(
        session, [assignment.name for assignment in assignments]
    )
    if not needing_reference:
        return

    rows_missing_expected_output = [row.id for row in rows if not row.expected_output]
    if rows_missing_expected_output:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Test types {sorted(needing_reference)} require a "
                   f"non-empty expected_output, but Dataset Rows with ids "
                   f"{[str(row_id) for row_id in rows_missing_expected_output]} have none",
        )
