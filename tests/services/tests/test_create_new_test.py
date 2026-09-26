import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from assay.schemas import CreateTestCaseRequest, TestTypeAssignment
from assay.services import create_new_test, create_new_test_from_dataset
from assay.services.tests._common import (
    _check_reference_required_types_have_expected_output_for_rows_or_422,
    _check_reference_required_types_have_expected_output_or_422,
    _validate_test_type_assignments,
)

name = "Test Name"
model_input = "My Input"
model_output = "The model output"
expected_output = "The expected model output"


def _catalogue_row(type_name, config_fields):
    row = MagicMock()
    row.name = type_name
    row.config_fields = config_fields
    return row


def test_create_new_test_without_name():
    request = CreateTestCaseRequest(
        input=model_input,
        model_output=model_output,
        expected_output=expected_output
    )
    uuid.UUID(request.name)

    mock_session = AsyncMock()

    response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    uuid.UUID(str(response.id))
    uuid.UUID(response.name)
    assert response.input == model_input
    assert response.model_output == model_output
    assert response.expected_output == expected_output


def test_create_new_test_with_name():
    request = CreateTestCaseRequest(
        name=name,
        input=model_input,
        model_output=model_output,
        expected_output=expected_output
    )
    with pytest.raises(ValueError):
        uuid.UUID(request.name)

    mock_session = AsyncMock()

    response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    assert response.name == name


def test_create_new_test_without_model_expected_output():
    request = CreateTestCaseRequest(
        name=name,
        input=model_input,
        model_output=None,
        expected_output=None
    )

    mock_session = AsyncMock()

    response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    assert response.model_output is None
    assert response.expected_output is None


def test_create_new_test_with_test_names():
    request = CreateTestCaseRequest(
        input=model_input,
        model_output=model_output,
        expected_output=expected_output,
        test_type_assignments=[
            TestTypeAssignment(name="ROUGE"),
            TestTypeAssignment(name="BERTScore"),
        ],
    )

    mock_session = AsyncMock()

    with patch(
            "assay.services.tests.create_new_test._validate_test_type_assignments",
            new=AsyncMock()
    ):
        response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    assert response.test_type_assignments == [
        TestTypeAssignment(name="ROUGE"),
        TestTypeAssignment(name="BERTScore"),
    ]


def test_create_new_test_raises_422_for_reference_required_type_with_no_expected_output():
    request = CreateTestCaseRequest(
        input=model_input,
        expected_output=None,
        test_type_assignments=[TestTypeAssignment(name="Exact Match")],
    )

    mock_session = AsyncMock()
    catalogue_row = MagicMock()
    catalogue_row.name = "Exact Match"
    catalogue_row.config_fields = [
        {"key": "reference", "label": "Expected output", "kind": "reference", "required": True}
    ]
    mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[catalogue_row]))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_not_called()
    assert exc.value.status_code == 422
    assert "Exact Match" in str(exc.value.detail)


def test_validate_raises_for_unknown_name():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(
            mock_session, [TestTypeAssignment(name="ROUGE")]
        ))
    assert exc.value.status_code == 422


def test_validate_passes_for_known_name_no_config_needed():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("ROUGE", [
            {"key": "reference", "label": "Reference text", "kind": "reference", "required": True}
        ])])
    )

    # ROUGE's only field is kind "reference" — never checked against config, so
    # a bare assignment with no config passes.
    asyncio.run(_validate_test_type_assignments(
        mock_session, [TestTypeAssignment(name="ROUGE")]
    ))  # no raise


def test_validate_raises_for_missing_required_config():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Regex Match", [
            {"key": "pattern", "label": "Regex pattern", "kind": "multiline", "required": True}
        ])])
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(
            mock_session, [TestTypeAssignment(name="Regex Match")]
        ))
    assert exc.value.status_code == 422
    assert "pattern" in str(exc.value.detail)


def test_validate_passes_with_required_config_provided():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Regex Match", [
            {"key": "pattern", "label": "Regex pattern", "kind": "multiline", "required": True}
        ])])
    )

    asyncio.run(_validate_test_type_assignments(
        mock_session,
        [TestTypeAssignment(name="Regex Match", config={"pattern": "^\\d+$"})],
    ))  # no raise


def test_validate_passes_for_optional_config_omitted():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Toxicity", [
            {"key": "rubric", "label": "Custom toxicity rubric",
             "kind": "rubric", "required": False}
        ])])
    )

    # rubric is optional — omitting config entirely still passes.
    asyncio.run(_validate_test_type_assignments(
        mock_session, [TestTypeAssignment(name="Toxicity")]
    ))  # no raise


def test_validate_raises_for_missing_required_threshold():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("ROUGE", [
            {"key": "reference", "label": "Reference text",
             "kind": "reference", "required": True},
            {"key": "threshold", "label": "Minimum score to pass",
             "kind": "numeric", "required": True},
        ])])
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_assignments(
            mock_session, [TestTypeAssignment(name="ROUGE")]
        ))
    assert exc.value.status_code == 422
    assert "threshold" in str(exc.value.detail)


def test_validate_does_not_check_threshold_format():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("ROUGE", [
            {"key": "reference", "label": "Reference text",
             "kind": "reference", "required": True},
            {"key": "threshold", "label": "Minimum score to pass",
             "kind": "numeric", "required": True},
        ])])
    )

    # note 8: the API only checks presence, never format — a non-numeric
    # string is the FE's problem to catch, not the API's.
    asyncio.run(_validate_test_type_assignments(
        mock_session,
        [TestTypeAssignment(name="ROUGE", config={"threshold": "not a number"})],
    ))  # no raise


# -- _check_reference_required_types_have_expected_output_or_422 --
# _validate_test_type_assignments deliberately never checks a "reference"
# field (it resolves from expected_output, not config — note 3), so this
# guard exists specifically to catch the gap that leaves: an assignment
# needing a reference with no expected_output anywhere to supply one.

def test_check_reference_raises_when_expected_output_missing():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Exact Match", [
            {"key": "reference", "label": "Expected output",
             "kind": "reference", "required": True},
        ])])
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_check_reference_required_types_have_expected_output_or_422(
            mock_session, [TestTypeAssignment(name="Exact Match")], None
        ))
    assert exc.value.status_code == 422
    assert "Exact Match" in str(exc.value.detail)


def test_check_reference_passes_when_expected_output_present():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Exact Match", [
            {"key": "reference", "label": "Expected output",
             "kind": "reference", "required": True},
        ])])
    )

    asyncio.run(_check_reference_required_types_have_expected_output_or_422(
        mock_session, [TestTypeAssignment(name="Exact Match")], "some answer"
    ))  # no raise


def test_check_reference_passes_for_types_with_no_reference_field():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Regex Match", [
            {"key": "pattern", "label": "Regex pattern",
             "kind": "multiline", "required": True},
        ])])
    )

    # Regex Match has no "reference" field at all — missing expected_output
    # is irrelevant to it, nothing to raise on.
    asyncio.run(_check_reference_required_types_have_expected_output_or_422(
        mock_session, [TestTypeAssignment(name="Regex Match", config={"pattern": "x"})], None
    ))  # no raise


# -- _check_reference_required_types_have_expected_output_for_rows_or_422 --

def test_check_reference_for_rows_raises_listing_every_offending_row():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Exact Match", [
            {"key": "reference", "label": "Expected output",
             "kind": "reference", "required": True},
        ])])
    )
    good_row = MagicMock(id=uuid.uuid4(), expected_output="has one")
    bad_row_1 = MagicMock(id=uuid.uuid4(), expected_output=None)
    bad_row_2 = MagicMock(id=uuid.uuid4(), expected_output="")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_check_reference_required_types_have_expected_output_for_rows_or_422(
            mock_session, [TestTypeAssignment(name="Exact Match")],
            [good_row, bad_row_1, bad_row_2],
        ))
    assert exc.value.status_code == 422
    assert str(bad_row_1.id) in str(exc.value.detail)
    assert str(bad_row_2.id) in str(exc.value.detail)
    assert str(good_row.id) not in str(exc.value.detail)


def test_check_reference_for_rows_passes_when_every_row_has_one():
    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[_catalogue_row("Exact Match", [
            {"key": "reference", "label": "Expected output",
             "kind": "reference", "required": True},
        ])])
    )
    rows = [MagicMock(id=uuid.uuid4(), expected_output="present") for _ in range(3)]

    asyncio.run(_check_reference_required_types_have_expected_output_for_rows_or_422(
        mock_session, [TestTypeAssignment(name="Exact Match")], rows
    ))  # no raise


# -- create_new_test_from_dataset

def _get_mock_request_with_id():
    # returns both the mock and the raw dataset id string
    mock_request_id = str(uuid.uuid4())
    return MagicMock(id=mock_request_id), mock_request_id


def _patch_rows(*rows):
    # shorthand for mocking the DB call that returns dataset rows
    return patch(
        "assay.services.tests.create_new_test._get_all_rows_or_404",
        new=AsyncMock(return_value=list(rows)),
    )


def _patch_validate_test_type_assignments():
    return patch("assay.services.tests.create_new_test._validate_test_type_assignments")


def _patch_check_reference_required_types_for_rows():
    # shorthand for tests that don't care about the expected_output/reference
    # check itself — see test_create_new_test.py's reference-specific tests
    # for that
    return patch(
        "assay.services.tests.create_new_test"
        "._check_reference_required_types_have_expected_output_for_rows_or_422",
        new=AsyncMock(),
    )


def _patch_next_new_test_number(start=1):
    # shorthand for tests that don't care about the numbering itself —
    # see test_names_start_at_1_when_none_exist_yet and
    # test_names_continue_globally_from_existing_new_test_names for that
    return patch(
        "assay.services.tests.create_new_test._next_new_test_number",
        new=AsyncMock(return_value=start),
    )


def _get_rows():
    return MagicMock(
        input="first", expected_output="y", model_output="z", id=uuid.uuid4()), MagicMock(
        input="second", expected_output="y", model_output="z", id=uuid.uuid4()), MagicMock(
        input="third", expected_output="y", model_output="z", id=uuid.uuid4()),

def test_row_exist_no_test_type_assignments():
    mock_request, mock_request_id = _get_mock_request_with_id()
    # [] is falsy, so the `if request.test_type_assignments:` branch is skipped entirely
    mock_request.test_type_assignments = []

    mock_session = AsyncMock()

    # _patch_validate_test_type_assignments is captured so we can assert it was never called
    with _patch_rows(*_get_rows()), \
            _patch_next_new_test_number(), \
            _patch_validate_test_type_assignments() as mock_validate_test_type_assignments:
        result = asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    # validation must be skipped when no test type assignments are provided
    mock_validate_test_type_assignments.assert_not_called()
    mock_session.commit.assert_called_once()
    # the response must carry back the original dataset id
    assert result.id == UUID(mock_request_id)
    # 3 rows in → 3 TestModels created → 3 ids in the response
    assert len(result.test_cases) == 3
    # add_all is called twice: first for tests, then for test_types.
    # [1] = second call, [0] = positional args tuple, [0] = the list passed in.
    # with no test_type_assignments, no TestTypeAssignmentModels should be created.
    assert mock_session.add_all.call_args_list[1][0][0] == []


def test_correct_number_of_test_type_assignments():
    mock_request, mock_request_id = _get_mock_request_with_id()
    # 2 test types will be assigned to each test
    mock_request.test_type_assignments = [
        TestTypeAssignment(name="ROUGE"),
        TestTypeAssignment(name="BERTScore"),
    ]

    mock_session = AsyncMock()

    # 3 rows → 3 TestModels, each assigned 2 test types → 6 TestTypeAssignmentModels
    with _patch_rows(*_get_rows()), \
            _patch_next_new_test_number(), \
            _patch_validate_test_type_assignments(), \
            _patch_check_reference_required_types_for_rows():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    # first add_all call is for tests: one per row
    assert len(mock_session.add_all.call_args_list[0][0][0]) == 3
    # second add_all call is for test_types: cartesian product of tests × assignments (3 × 2)
    assert len(mock_session.add_all.call_args_list[1][0][0]) == 6


def test_correct_mapping():
    mock_request, mock_request_id = _get_mock_request_with_id()

    mock_session = AsyncMock()

    with _patch_rows(*_get_rows()), \
            _patch_next_new_test_number(), \
            _patch_validate_test_type_assignments(), \
            _patch_check_reference_required_types_for_rows():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    # mapping is the same for every row, so checking one is enough.
    # [0] = first add_all call (tests), [0] = positional args, [0] = the list, [0] = first TestModel
    single_test = mock_session.add_all.call_args_list[0][0][0][0]
    assert single_test.input == "first"
    assert single_test.expected_output == "y"
    assert single_test.model_output == "z"


def test_names_start_at_1_when_none_exist_yet():
    mock_request, _ = _get_mock_request_with_id()

    mock_session = AsyncMock()
    mock_session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with _patch_rows(*_get_rows()), \
            _patch_validate_test_type_assignments(), \
            _patch_check_reference_required_types_for_rows():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    created_tests = mock_session.add_all.call_args_list[0][0][0]
    assert [test.name for test in created_tests] == [
        "New Test 1", "New Test 2", "New Test 3"
    ]


def test_names_continue_globally_from_existing_new_test_names():
    mock_request, _ = _get_mock_request_with_id()

    mock_session = AsyncMock()
    # a prior import already created "New Test 1"/"New Test 2"; a manually
    # renamed test that doesn't match the pattern must be ignored, not
    # counted, and must not derail the max() calculation
    mock_session.scalars.return_value = MagicMock(all=MagicMock(
        return_value=["New Test 1", "New Test 2", "Renamed by a user"]
    ))

    with _patch_rows(*_get_rows()), \
            _patch_validate_test_type_assignments(), \
            _patch_check_reference_required_types_for_rows():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    created_tests = mock_session.add_all.call_args_list[0][0][0]
    assert [test.name for test in created_tests] == [
        "New Test 3", "New Test 4", "New Test 5"
    ]


def test_database_not_found():
    mock_session = AsyncMock()
    mock_request, _ = _get_mock_request_with_id()

    # force _get_dataset_or_404 to raise 404 via side_effect — execution stops there,
    # the session and all subsequent steps are never reached
    with patch(
            "assay.services.tests.create_new_test._get_dataset_or_404",
            new=AsyncMock(side_effect=HTTPException(status_code=404)),
    ), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    assert exc.value.status_code == 404


def test_no_dataset_rows():
    mock_session = AsyncMock()
    # make session.scalars(...).all() return [] so the real _get_all_rows_or_404 raises 404
    mock_session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    mock_request, _ = _get_mock_request_with_id()

    # safety net: _get_dataset_or_404 would pass anyway since AsyncMock is always truthy,
    # but patching makes the intent explicit and avoids relying on that side effect
    with patch("assay.services.tests.create_new_test._get_dataset_or_404"), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    assert exc.value.status_code == 404


def test_unknown_test_type_name():
    mock_session = AsyncMock()
    # simulate the test types catalogue returning no matches — _validate_test_type_assignments
    # computes the difference between requested names and found names, and raises 422 if non-empty
    mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    mock_request, _ = _get_mock_request_with_id()
    mock_request.test_type_assignments = [TestTypeAssignment(name="SHOULD FAIL")]

    # _get_dataset_or_404 is patched as a safety net (AsyncMock is always truthy)
    # _patch_rows lets execution reach _validate_test_type_assignments, where the 422 is raised
    with patch("assay.services.tests.create_new_test._get_dataset_or_404"), \
            _patch_rows(*_get_rows()), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    assert exc.value.status_code == 422


def test_dataset_import_422_when_any_row_missing_expected_output_for_reference_required_type():
    mock_request, _ = _get_mock_request_with_id()
    mock_request.test_type_assignments = [TestTypeAssignment(name="Exact Match")]

    good_row = MagicMock(input="ok", expected_output="present",
                         model_output="z", id=uuid.uuid4())
    bad_row = MagicMock(input="missing ref", expected_output=None,
                        model_output="z", id=uuid.uuid4())

    mock_session = AsyncMock()
    catalogue_row = MagicMock()
    catalogue_row.name = "Exact Match"
    catalogue_row.config_fields = [
        {"key": "reference", "label": "Expected output", "kind": "reference", "required": True}
    ]
    mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[catalogue_row]))

    with _patch_rows(good_row, bad_row), \
            _patch_validate_test_type_assignments(), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    mock_session.commit.assert_not_called()
    assert exc.value.status_code == 422
    assert str(bad_row.id) in str(exc.value.detail)
    assert str(good_row.id) not in str(exc.value.detail)
