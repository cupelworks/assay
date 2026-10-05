# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.assignment_labels import in_label_order
from assay.assignment_labels import labelled as _labelled
from assay.models import TestModel, TestTypeAssignmentModel
from assay.schemas import (
    CreateTestCaseFromDatasetRequest,
    CreateTestCaseFromDatasetResponse,
    CreateTestCaseRequest,
    CreateTestCaseResponse,
    RecordedAnswers,
    TestCaseID,
    TestNaming,
)
from assay.services.datasets._common import _get_all_rows_or_404, _get_dataset_or_404
from assay.services.tests._common import (
    _check_reference_required_types_have_expected_output_for_rows_or_422,
    _check_reference_required_types_have_expected_output_or_422,
    _validate_test_type_assignments,
)

logger = logging.getLogger(__name__)

_NEW_TEST_NAME_PATTERN = re.compile(r"^New Test (\d+)$")


async def _next_new_test_number(session: AsyncSession) -> int:
    """Find the next number to use for a "New Test <n>" default name.

    Scans every existing test name matching the "New Test <n>" pattern
    exactly and returns one past the highest number found — global across
    the whole tests table, not scoped to any one dataset or import, so a
    second import doesn't restart at "New Test 1" and collide with names
    the first one already used. A name that doesn't match the pattern
    exactly (including one a user has since renamed) is ignored, not
    counted — this only ever looks at the pattern's own numbering, never
    at how many tests exist in total.

    Args:
        session: Active async database session.

    Returns:
        The next number to use — 1 if no matching name exists yet.
    """
    existing_names = (await session.scalars(
        select(TestModel.name).where(TestModel.name.like("New Test %"))
    )).all()

    existing_numbers = [
        int(match.group(1))
        for name in existing_names
        if (match := _NEW_TEST_NAME_PATTERN.match(name))
    ]

    return max(existing_numbers, default=0) + 1


async def create_new_test(
        request: CreateTestCaseRequest,
        session: AsyncSession) -> CreateTestCaseResponse:
    """Orchestrates test case creation: persists the model and returns the result.

    Validates test type assignments against the catalogue before inserting.
    Raises HTTP 422 if any name is unknown, a required config field is
    missing, or an assigned type needs a reference (expected_output) that
    wasn't provided.

    Args:
        request: Request containing the test name, input, optional expected/model outputs,
            and optional list of test type assignments.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The created test case with its generated ID and all input fields.
    """
    if request.test_type_assignments:
        await _validate_test_type_assignments(session, request.test_type_assignments)
        await _check_reference_required_types_have_expected_output_or_422(
            session, request.test_type_assignments, request.expected_output
        )
    assignments = in_label_order(_labelled(request.test_type_assignments))

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
            label=assignment.label,
            config=assignment.config,
            answer_path=assignment.answer_path,
        )
        for assignment in assignments
    ]

    session.add(test)
    session.add_all(test_types)
    await session.commit()

    logger.info(
        "Created test %s (%r) with %d test type assignments",
        test.id, test.name, len(test_types),
        extra={"test_id": test.id, "test_type_count": len(test_types)},
    )
    return CreateTestCaseResponse(
        id=test.id,
        name=test.name,
        input=test.input,
        model_output=test.model_output,
        expected_output=test.expected_output,
        test_type_assignments=assignments,
    )


NAME_LENGTH = 60


def _name_from_prompt(prompt: str) -> str:
    """A test's name from its row's prompt: line breaks and runs of spaces made
    one space; over 60 characters, cut after the last whole word within the
    first 59 (or at 59 when there's no space), and "…" appended."""
    text = " ".join(prompt.split())
    if len(text) <= NAME_LENGTH:
        return text
    head = text[:NAME_LENGTH - 1]
    if text[NAME_LENGTH - 1] != " " and " " in head:
        head = head[:head.rindex(" ")]
    return head.rstrip() + "…"


def _recorded_answer(model_output: str | None) -> str | None:
    """A dataset row's `model_output` as the test's recorded answer: a blank one (empty or
    only whitespace) is no recorded answer, so the test's runs ask the application under
    test."""
    return model_output if model_output and model_output.strip() else None


async def create_new_test_from_dataset(
        request: CreateTestCaseFromDatasetRequest,
        session: AsyncSession) -> CreateTestCaseFromDatasetResponse:
    """Create test cases in bulk from all rows of an existing dataset.

    A row whose `model_output` is blank (empty or only whitespace) makes a
    test with no recorded answer, so its runs ask the application under test.

    Each created test is named "New Test <n>", numbered globally across the
    whole tests table (see _next_new_test_number) — dataset rows have no
    name of their own to reuse, and a random UUID (the manual-creation
    default) is unreadable at a glance across dozens of rows. Numbering is
    global, not scoped to this batch or dataset, so re-running an import
    continues from the highest "New Test <n>" that already exists instead
    of restarting at 1 and duplicating a name already in use.

    Args:
        request: Dataset ID and optional list of test type assignments.
        session: Active async database session.

    Returns:
        `CreateTestCaseFromDatasetResponse` with the dataset ID and the IDs of all created tests.

    Raises:
        HTTPException: 404 if the dataset or its rows are not found.
        HTTPException: 422 if any test type name is unknown, a required
            config field is missing, or any row's expected_output is empty
            while a reference-requiring type is assigned — the same
            assignments apply to every row, so this checks every row's own
            expected_output, not just the request's, and reports every
            offending row together (all-or-nothing, no partial import).
    """
    await _get_dataset_or_404(request.id, session)
    rows = await _get_all_rows_or_404(request.id, session, request.row_ids)

    if request.test_type_assignments:
        await _validate_test_type_assignments(session, request.test_type_assignments)
        await _check_reference_required_types_have_expected_output_for_rows_or_422(
            session, request.test_type_assignments, rows
        )
    assignments = in_label_order(_labelled(request.test_type_assignments))

    next_number = await _next_new_test_number(session)

    tests = [
        TestModel(
            id=uuid.uuid4(),
            dataset_row_id=row.id,
            name=(_name_from_prompt(row.input) if request.naming == TestNaming.prompt
                  else f"New Test {next_number + offset}"),
            input=row.input,
            model_output=(None if request.recorded_answers == RecordedAnswers.leave_out
                          else _recorded_answer(row.model_output)),
            expected_output=row.expected_output,
        )
        for offset, row in enumerate(rows)
    ]

    test_types = [
        TestTypeAssignmentModel(
            test_id=test.id,
            test_type_name=assignment.name,
            label=assignment.label,
            config=assignment.config,
            answer_path=assignment.answer_path,
        )
        for test in tests for assignment in assignments
    ]

    session.add_all(tests)
    session.add_all(test_types)
    await session.commit()

    logger.info(
        "Created %d tests from dataset %s", len(tests), request.id,
        extra={"dataset_id": request.id, "test_count": len(tests)},
    )
    return CreateTestCaseFromDatasetResponse(
        test_cases=[
            TestCaseID(
                id=test.id
            )
            for test in tests
        ],
        id=request.id
    )
