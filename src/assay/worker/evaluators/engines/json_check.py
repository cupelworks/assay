# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""JSON checks: the answer is JSON, and — depending on the row — matches a
schema or has an expected value at a path.

Row settings: `check` picks what's verified after parsing — `valid` (the
answer parses), `schema` (it satisfies the assignment's `schema`, a JSON
Schema written as JSON) or `field` (the value at the assignment's JSONPath
`path` equals its `value`, written as JSON). `strip_fences`, on by default,
reads the JSON out of a Markdown code fence when the whole answer is one
(```json … ```), which LLMs add routinely; prose around the JSON is still a
failure, since an application that should return JSON didn't.

`field` compares parsed values, type for type: `"42"` is not `42`, `true`
is not `1`, and key order in an object doesn't matter. When the path
matches several values, the first one is compared.

An invalid schema, path or expected value raises with the reason — the
error is the user's feedback, like an invalid regex; it is never validated
at write time.

No `score`: a JSON check is pass/fail by nature, with no scale to measure
on, so `passed` is the whole result.
"""
import json
import re
from collections.abc import Callable
from typing import Any

from jsonpath_ng import parse as parse_jsonpath
from jsonschema import validators
from jsonschema.exceptions import SchemaError, best_match

from assay.schemas import EvaluationInput, TestTypeResult

# The whole answer is one fenced block: ```json (or just ```) … ```
_FENCED = re.compile(r"\A\s*```[A-Za-z]*[ \t]*\n(.*?)\n?[ \t]*```\s*\Z", re.DOTALL)
# How much of a value goes into a failure's detail
_MAX_SHOWN = 80


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    check = evaluation.engine_settings.get("check", "valid")
    run_check = _CHECKS.get(check)
    if run_check is None:
        raise ValueError(f"Unknown JSON check {check!r} on this test type's catalogue row")

    text = evaluation.answer
    if evaluation.engine_settings.get("strip_fences", True):
        fenced = _FENCED.match(text)
        if fenced:
            text = fenced.group(1)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        return _fail(f"The answer is not valid JSON: {exc}")

    return run_check(document, evaluation.config)


def _valid(_document: Any, _config: dict[str, str]) -> TestTypeResult:
    return TestTypeResult(passed=True, score=None, detail=None)


def _schema(document: Any, config: dict[str, str]) -> TestTypeResult:
    schema = _parse_config(config, "schema", "The schema")
    if not isinstance(schema, dict | bool):
        # validator_for would fail with a TypeError that says nothing useful
        raise ValueError("The schema is not a valid JSON Schema: it must be a JSON object "
                         "or a boolean")
    validator_class = validators.validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"The schema is not a valid JSON Schema: {exc.message}") from None

    error = best_match(validator_class(schema).iter_errors(document))
    if error is None:
        return TestTypeResult(passed=True, score=None, detail=None)
    where = "" if error.json_path == "$" else f" at {error.json_path}"
    return _fail(f"Doesn't match the schema{where}: {error.message}")


def _field(document: Any, config: dict[str, str]) -> TestTypeResult:
    path = config.get("path")
    if not path:
        raise ValueError("No JSONPath configured for this test type")
    try:
        compiled = parse_jsonpath(path)
    except Exception as exc:  # jsonpath-ng's parser raises assorted exception types
        raise ValueError(f"JSONPath {path!r} is not valid: {exc}") from None
    expected = _parse_config(config, "value", "The expected value",
                             hint=' — write strings in double quotes, e.g. "approved"')

    matches = compiled.find(document)
    if not matches:
        return _fail(f"Nothing found at {path}")
    actual = matches[0].value
    if _equal(actual, expected):
        return TestTypeResult(passed=True, score=None, detail=None)
    return _fail(f"{path} is {_shown(actual)}, expected {_shown(expected)}")


_CHECKS: dict[str, Callable[[Any, dict[str, str]], TestTypeResult]] = {
    "valid": _valid,
    "schema": _schema,
    "field": _field,
}


def _parse_config(config: dict[str, str], key: str, what: str, hint: str = "") -> Any:
    raw = config.get(key)
    if raw is None or not raw.strip():
        raise ValueError(f"No {key} configured for this test type")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} is not valid JSON: {exc}{hint}") from None


def _equal(a: Any, b: Any) -> bool:
    """JSON equality: numbers compare by value (1 == 1.0), everything else by
    type as well — Python's own == would make True equal to 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and a == b


def _shown(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= _MAX_SHOWN else text[:_MAX_SHOWN - 1] + "…"


def _fail(detail: str) -> TestTypeResult:
    return TestTypeResult(passed=False, score=None, detail=detail)
