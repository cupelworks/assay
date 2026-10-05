# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from assay.assignment_labels import in_label_order
from assay.assignment_labels import labelled as _labelled
from assay.models import TestModel, TestSetEntryModel, TestTypeAssignmentModel
from assay.schemas import CreateTestCaseResponse, ModifyTestCaseRequest
from assay.services.tests._common import (
    _assignment_schema,
    _check_reference_required_types_have_expected_output_or_422,
    _find_test_by_id_or_404,
    _validate_test_type_assignments,
)

logger = logging.getLogger(__name__)

# The fields a PATCH can empty: sent as null, they're cleared; left out of the
# body, they're untouched. Both reach the request object as None, so what tells
# them apart is request.model_fields_set — the fields the client actually sent.
# Only nullable columns belong here: a null written to name or input (NOT NULL)
# would fail at commit. It's an allow-list, so a field added to the request
# later isn't clearable until someone decides it should be.
_CLEARABLE_FIELDS = ("expected_output", "model_output")
# Every name above must be a field of the request (<= is "is a subset of"), so
# renaming one in the schema without updating the tuple fails at import time
# instead of silently making that field unclearable.
assert set(_CLEARABLE_FIELDS) <= set(ModifyTestCaseRequest.model_fields)

def _apply_scalar_updates(
        found: TestModel | TestSetEntryModel,
        request: ModifyTestCaseRequest
) -> None:
    """Write the request's scalar fields onto the test case or entry.

    A field left out of the body is never touched. A field in
    _CLEARABLE_FIELDS (expected_output, model_output) is written whenever
    it was sent — null included, which clears it. name and input are
    written only when sent with a value: neither can be empty, so a null
    for either changes nothing.

    Args:
        found: The test case or test set entry being updated.
        request: Partial update payload.
    """
    if request.name is not None:
        found.name = request.name
    if request.input is not None:
        found.input = request.input
    for field in _CLEARABLE_FIELDS:
        if field in request.model_fields_set:
            setattr(found, field, getattr(request, field))


def _effective_expected_output(
        found: TestModel | TestSetEntryModel,
        request: ModifyTestCaseRequest,
) -> str | None | Mapped[str | None]:
    """expected_output as it will be once the request is applied: the sent
    value if the request sends one — null included — the current one
    otherwise. The same rule _apply_scalar_updates writes by."""
    if "expected_output" in request.model_fields_set:
        return request.expected_output
    return found.expected_output


def _applied_fields(request: ModifyTestCaseRequest) -> list[str]:
    """The fields the request changes, sorted: every clearable field it sent,
    null included, and every other field it sent with a value."""
    return sorted(
        field for field in request.model_fields_set
        if field in _CLEARABLE_FIELDS or getattr(request, field) is not None
    )


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
                label=assignment.label,
                config=assignment.config,
                answer_path=assignment.answer_path,
            )
            for assignment in in_label_order(_labelled(request.test_type_assignments))
        ]


async def modify_test_by_id(
        test_case_id: uuid.UUID,
        request: ModifyTestCaseRequest,
        session: AsyncSession,
) -> CreateTestCaseResponse:
    """Partially update a test case and return the full updated record.

    Only fields present in the body are written — omitted fields are left unchanged.
    expected_output and model_output sent as null are cleared; name and input sent as
    null are left as they are. For test_type_assignments: null leaves assignments
    untouched, while [] removes all existing assignments.

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
    effective_expected_output = _effective_expected_output(found, request)
    effective_assignments = (
        request.test_type_assignments if request.test_type_assignments is not None
        else [
            _assignment_schema(a)
            for a in found.test_type_assignments
        ]
    )
    await _check_reference_required_types_have_expected_output_or_422(
        session, effective_assignments, effective_expected_output
    )

    _apply_scalar_updates(found, request)
    await _apply_test_type_assignments_update(found, request, session)

    await session.commit()

    changed_fields = _applied_fields(request)
    logger.info(
        "Updated test %s (fields: %s)", found.id, ", ".join(changed_fields),
        extra={"test_id": found.id, "fields": changed_fields},
    )
    return CreateTestCaseResponse(
        id=found.id,
        name=found.name,
        input=found.input,
        model_output=found.model_output,
        expected_output=found.expected_output,
        test_type_assignments=in_label_order(
            [_assignment_schema(a) for a in found.test_type_assignments]),
    )
