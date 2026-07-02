import asyncio
import uuid
from io import StringIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.schemas import DataSetRowSchema
from assay.services.datasets._common import _check_name_unique
from assay.services.datasets.upload_full_dataset import (
    _build_dataset_model,
    _build_response,
    _check_file_exists,
    _parse_and_validate_rows,
    _persist_dataset,
    _raise_if_errors,
)

# --- _check_file_exists ---

def test_check_file_exists_existing(tmp_path):
    # tmp_path is a pytest built-in fixture: creates a temp dir before the test, deletes it after.
    f = tmp_path / "test.jsonl"
    f.touch()

    _check_file_exists(str(f))


def test_check_file_not_existing(tmp_path):
    f = tmp_path / "test.jsonl"

    with pytest.raises(HTTPException) as e:
        _check_file_exists(str(f))

    assert e.value.status_code == 404
    assert e.value.detail == f"File not found: {str(f)}"


# --- _build_dataset_model ---

dataset_name = "Test Dataset"
prompt = "Prompt"
expected_output = "Expected Output"
model_output = "Model Output"

def test_build_dataset_model_correct_name():
    model = _build_dataset_model(dataset_name, [])

    assert model.name == dataset_name


def test_build_dataset_model_rows_mapping():
    data_row = DataSetRowSchema(prompt=prompt,
                                expected_output=expected_output,
                                model_output=model_output)

    model_rows = _build_dataset_model("", [data_row]).rows
    assert len(model_rows) == 1
    assert model_rows[0].input == prompt
    assert model_rows[0].expected_output == expected_output
    assert model_rows[0].model_output == model_output


def test_build_dataset_model_multiple_rows():
    data_rows = [DataSetRowSchema(prompt=prompt,
                                  expected_output=expected_output,
                                  model_output=model_output) for _ in range(3)]

    model_rows = _build_dataset_model("", data_rows).rows
    assert len(model_rows) == 3


# --- _raise_if_errors ---

def test_raise_if_errors_no_errors():
    errors = []
    _raise_if_errors(errors)


def test_raise_if_errors_one_error():
    line_n = 5
    content = "testing content"
    errs = ["error 1", "error 2", "error 3"]
    errors = [(line_n, content, errs)]

    with pytest.raises(HTTPException) as e:
        _raise_if_errors(errors)

    assert e.value.status_code == 422
    assert e.value.detail == [{"line": line_n, "content": content.strip(), "errors": errs}]


# --- _parse_and_validate_rows ---

def test_parse_and_validate_rows_no_errors():
    content = StringIO('{"prompt": "p", "expected_output": "e", "model_output": "m"}\n')

    rows, errors = _parse_and_validate_rows(content)
    assert len(rows) == 1
    assert len(errors) == 0
    

def test_parse_and_validate_rows_with_error():
    content = StringIO('{"prompt": "p", "expected_output": "e"}\n')

    rows, errors = _parse_and_validate_rows(content)
    assert len(rows) == 0
    assert len(errors) == 1
    

def test_parse_and_validate_rows_blank_lines():
    content = StringIO('\n{"prompt": "p", "expected_output": "e", "model_output": "m"}\n\n')

    rows, errors = _parse_and_validate_rows(content)
    assert len(rows) == 1
    assert len(errors) == 0
    

def test_parse_and_validate_rows_no_error_plus_error():
    content = StringIO('\n{"prompt": "p", "expected_output": "e", "model_output": "m"}'
                       '\n\n{"prompt": "p", "expected_output": "e"}\n')

    rows, errors = _parse_and_validate_rows(content)
    assert len(rows) == 1
    assert len(errors) == 1


# --- _persist_dataset ---

def test_persist_dataset_session():
    session = AsyncMock()
    model = MagicMock()

    asyncio.run(_persist_dataset(model, session))
    session.add.assert_called_once()
    session.flush.assert_called_once()
    session.commit.assert_called_once()


def test_persist_dataset_dataset_id_correct():
    session = AsyncMock()
    model = MagicMock()
    model.id = 123

    dataset_id, _ = asyncio.run(_persist_dataset(model, session))
    assert dataset_id == model.id


def test_persist_dataset_row_ids_correct():
    session = AsyncMock()
    model = MagicMock()
    row1, row2 = MagicMock(), MagicMock()
    row1.id = 1
    row2.id = 2
    model.rows = [row1, row2]

    _, row_ids = asyncio.run(_persist_dataset(model, session))

    assert row_ids == [1, 2]


# --- _build_response ---

req = MagicMock()
req.path = "testing/path.jsonl"
req.dataset_name = "testing dataset"

def test_build_response_path_matched_req_path():
    dataset_id = uuid.uuid4()
    rows = [MagicMock()]
    row_ids = [uuid.uuid4()]

    response = _build_response(req, rows, dataset_id, row_ids)
    assert response.path == req.path


def test_build_response_matched_dataset_name_id():
    dataset_id = uuid.uuid4()
    rows = [MagicMock()]
    row_ids = [uuid.uuid4()]

    response = _build_response(req, rows, dataset_id, row_ids)
    assert response.dataset.name == req.dataset_name
    assert response.dataset.id == dataset_id


def test_build_response_matched_rows():
    dataset_id = uuid.uuid4()
    rows = [MagicMock() for _ in range(5)]
    row_ids = [uuid.uuid4() for _ in range(5)]

    response = _build_response(req, rows, dataset_id, row_ids)
    assert response.loaded.n == len(row_ids)
    assert response.loaded.ids == row_ids
