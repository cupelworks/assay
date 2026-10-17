# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""POST /datasets/upload and GET /datasets/upload: the parsing rules on their
own, and the endpoint on a real database."""
import importlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from assay.config import settings
from assay.schemas import DataSetUploadRefused
from assay.services.datasets.upload_dataset import _parse_lines

# The module, not the package's function of the same name
upload_module = importlib.import_module("assay.services.datasets.upload_dataset")

ROW = {"prompt": "p", "expected_output": "e", "model_output": "m"}


def _line(**fields) -> str:
    return json.dumps({**ROW, **fields})


def _upload(db, content: str | bytes, name="Uploaded"):
    return db.client.post("/datasets/upload", data={"dataset_name": name},
                          files={"file": ("rows.jsonl", content)})


# --- the lines ---


def test_every_non_blank_line_is_a_row_in_order():
    rows, invalid_count, listed = _parse_lines(
        _line(prompt="a") + "\n\n" + _line(prompt="b") + "\n   \n" + _line(prompt="c") + "\n")

    assert [row.prompt for row in rows] == ["a", "b", "c"]
    assert (invalid_count, listed) == (0, [])


@pytest.mark.parametrize("break_", ["\n", "\r\n", "\r"])
def test_lines_are_numbered_from_1_counting_blank_ones_whatever_the_line_ending(break_):
    text = break_.join([_line(), "", "not json", _line()])

    _, _, listed = _parse_lines(text)

    assert [error.line for error in listed] == [3]


@pytest.mark.parametrize(("line", "msg"), [
    ("not json", "Not valid JSON (near character 2)."),
    ("[1, 2]", "Not a JSON object."),
    (json.dumps({"prompt": "p", "model_output": "m"}), "expected_output is missing."),
    (_line(prompt=1), "prompt must be text."),
])
def test_each_problem_is_said_in_plain_words(line, msg):
    _, _, listed = _parse_lines(line)

    assert listed[0].msg == msg


def test_a_line_with_several_problems_says_each():
    _, _, listed = _parse_lines(json.dumps({"prompt": 1, "model_output": "m"}))

    assert listed[0].msg == "prompt must be text. expected_output is missing."
    assert [(e.loc, e.type) for e in listed[0].errors] == [
        (["prompt"], "string_type"), (["expected_output"], "missing")]


def test_a_problem_never_carries_the_lines_content():
    _, _, listed = _parse_lines(_line(prompt=["a secret"]))

    assert "a secret" not in listed[0].model_dump_json()


def test_the_first_100_invalid_lines_are_listed_and_all_are_counted():
    rows, invalid_count, listed = _parse_lines("\n".join(["bad"] * 150 + [_line()]))

    assert (len(rows), invalid_count, len(listed)) == (1, 150, 100)
    assert listed[-1].line == 100


# --- the endpoint ---


def test_an_upload_creates_the_dataset_with_its_rows_numbered_in_order(db):
    response = _upload(db, "﻿" + _line(prompt="first") + "\n\n" + _line(prompt="second"))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["dataset"]["name"] == "Uploaded"
    assert body["loaded"]["n"] == 2 and len(body["loaded"]["ids"]) == 2
    rows = db.client.get(f"/datasets/{body['dataset']['id']}/rows").json()["items"]
    assert [(row["number"], row["row_info"]["prompt"]) for row in rows] == [
        (1, "first"), (2, "second")]


def test_a_file_of_blank_lines_creates_an_empty_dataset(db):
    response = _upload(db, "\n\n")

    assert response.status_code == 201, response.text
    assert response.json()["loaded"] == {"n": 0, "ids": []}


def test_invalid_lines_import_nothing_and_say_which_and_why(db):
    response = _upload(db, "\n".join([_line(), "not json", _line(), "[1]"]))

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "msg": "2 lines aren't valid dataset rows. Nothing was imported.",
        "invalid_lines": 2,
        "lines": [
            {"line": 2, "msg": "Not valid JSON (near character 2).", "errors": [
                {"loc": [], "msg": "Invalid JSON: expected ident at line 1 column 2",
                 "type": "json_invalid"}]},
            {"line": 4, "msg": "Not a JSON object.", "errors": [
                {"loc": [], "msg": "Input should be an object", "type": "model_type"}]},
        ],
    }
    assert db.client.get("/datasets").json()["total"] == 0


def test_one_invalid_line_is_said_in_the_singular(db):
    response = _upload(db, "oops")

    assert response.json()["detail"]["msg"] == (
        "1 line isn't a valid dataset row. Nothing was imported.")


def test_a_file_that_isnt_utf8_is_a_422_saying_so(db):
    response = _upload(db, "prompt: caf\xe9".encode("latin-1"))

    assert response.status_code == 422
    assert response.json() == {"detail": "The file isn't UTF-8 text."}


def test_a_taken_name_is_a_409_before_the_file_is_read(db):
    db.dataset("Taken", ["p"])

    response = _upload(db, "not even json", name="Taken")

    assert response.status_code == 409
    assert response.json() == {"detail": "Dataset name 'Taken' already exists."}


def test_a_file_over_the_limit_is_a_413(db, monkeypatch):
    monkeypatch.setattr(settings, "dataset_upload_max_mb", 1)
    monkeypatch.setattr(upload_module, "_CHUNK_BYTES", 1024)

    response = _upload(db, _line() + "\n" + "x" * (1024 * 1024))

    assert response.status_code == 413
    assert response.json() == {"detail": "The file is larger than 1 MB."}
    assert db.client.get("/datasets").json()["total"] == 0


@pytest.mark.parametrize(("data", "files", "missing"), [
    ({}, {"file": ("rows.jsonl", "")}, "dataset_name"),
    ({"dataset_name": "D"}, None, "file"),
])
def test_a_missing_part_is_fastapis_422(db, data, files, missing):
    response = db.client.post("/datasets/upload", data=data, files=files)

    assert response.status_code == 422
    assert [e["loc"] for e in response.json()["detail"]] == [["body", missing]]


@pytest.mark.parametrize("name", ["", "n" * 256])
def test_a_name_empty_or_over_255_characters_is_a_422(db, name):
    response = _upload(db, _line(), name=name)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "dataset_name"]


def test_the_limit_is_read_in_bytes(db, monkeypatch):
    monkeypatch.setattr(settings, "dataset_upload_max_mb", 3)

    response = db.client.get("/datasets/upload")

    assert response.status_code == 200
    assert response.json() == {"max_bytes": 3 * 1024 * 1024}


def test_the_path_import_is_gone(db):
    response = db.client.post("/datasets/path", json={"path": "/etc/x.jsonl",
                                                       "dataset_name": "D"})

    assert response.status_code in (404, 405)


@pytest.mark.parametrize("content", [b"not json", b"caf\xe9", None])
def test_every_422_matches_its_documented_schema(db, content):
    response = (db.client.post("/datasets/upload", data={"dataset_name": "D"})
                if content is None else _upload(db, content))

    assert response.status_code == 422
    TypeAdapter(DataSetUploadRefused).validate_python(response.json())


def test_the_documented_schema_refuses_an_error_without_loc_msg_or_type():
    with pytest.raises(ValidationError):
        TypeAdapter(DataSetUploadRefused).validate_python({"detail": [{"msg": "Field required"}]})
