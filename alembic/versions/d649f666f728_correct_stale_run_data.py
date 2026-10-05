# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""correct stale run data: results, messages and runs a fixed bug spoiled

Revision ID: d649f666f728
Revises: 45dabe26c054
Create Date: 2026-10-01 18:00:00.000000

Assay isn't in production yet, so nothing needs the old shapes: every
stored run and check is brought to what the current code writes, instead of
keeping what older code wrote "as the record".

Every result in test_runs.results:
- has every current key - passed, score, detail, engine, engine_settings,
  answer_path, rubric, judge - with null where nothing was recorded;
- of a deterministic type has score null (run_execution note 16: pass/fail
  checks report no score; Exact Match, Contains and Regex Match once wrote
  1.0/0.0);
- of an Exact Match, Contains or Regex Match type records
  normalize_lookalikes: false when its settings predate it - which is how it
  was scored;
- is keyed "Exact Match (whitespace-sensitive)" where it was still
  "Exact Match (strict)", the type's old name;
- has a detail starting with a capital letter (run_execution note 17).

Every run's error and every target and judge check's error starts with a
capital letter, and the application adapter's old wording, which named
ASSAY_TARGET_* variables, becomes today's - the settings may come from the
UI now.

Runs whose judge check failed with "'str' object is not callable" go back
to Pending, with everything a run writes cleared: that failure was a bug in
how a run handed its judge settings to the judge client, not an outcome, and
the reconciliation scan re-publishes a stale Pending run, so a worker scores
it again.

There's no downgrade: the old values were wrong, not an alternative.

"""
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd649f666f728'
down_revision: str | None = '45dabe26c054'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESULT_KEYS = ("passed", "score", "detail", "engine", "engine_settings", "answer_path",
                "rubric", "judge")
_DETERMINISTIC_ENGINES = {"exact_match", "contains", "regex", "json", "length"}
_TEXT_ENGINES = {"exact_match", "contains", "regex"}
_RENAMED = {"Exact Match (strict)": "Exact Match (whitespace-sensitive)"}
_JUDGE_BUG = "'str' object is not callable"

# the adapter's wording before its settings could come from the UI
_OLD_WORDING = [
    (re.compile(r"^no application configured: ASSAY_TARGET_URL is unset$"),
     "No application configured: no URL is set"),
    (re.compile(r"^ASSAY_TARGET_HEADERS references \$\{(\w+)\} but \w+ is not set$"),
     r"A header references ${\1} but \1 is not set on this server"),
    (re.compile(r"^ASSAY_TARGET_OUTPUT_PATH (.+?) is not a valid JSONPath: (.*)$", re.S),
     r"Output path \1 is not a valid JSONPath: \2"),
    (re.compile(r"^application's reply is not JSON$"), "The application's reply is not JSON"),
    (re.compile(r"^nothing found at ASSAY_TARGET_OUTPUT_PATH (.+?) in the application's "
                r"reply$"),
     r"Nothing found at output path \1 in the application's reply"),
    (re.compile(r"^the value at ASSAY_TARGET_OUTPUT_PATH (.+?) is (\w+), not a text answer$"),
     r"The value at output path \1 is \2, not a text answer"),
]

_test_runs = sa.table(
    "test_runs",
    sa.column("id", sa.Uuid()),
    sa.column("status", sa.Text()),
    sa.column("error", sa.Text()),
    sa.column("results", sa.JSON()),
    sa.column("executed_at", sa.DateTime()),
    sa.column("evaluated_output", sa.Text()),
    sa.column("output_source", sa.Text()),
    sa.column("application_reply", sa.JSON()),
)
_test_types = sa.table("test_types", sa.column("name", sa.Text()),
                       sa.column("category", sa.Text()))


def _sentence(text):
    return text[:1].upper() + text[1:] if isinstance(text, str) else text


def _message(text):
    """A stored error in today's wording, starting with a capital letter."""
    if not isinstance(text, str):
        return text
    for pattern, replacement in _OLD_WORDING:
        text = pattern.sub(replacement, text)
    return _sentence(text)


def _result(name: str, result: dict, deterministic_types: set[str]) -> dict:
    result = {key: result.get(key) for key in _RESULT_KEYS}
    engine = result["engine"]
    if engine in _DETERMINISTIC_ENGINES or (engine is None and name in deterministic_types):
        result["score"] = None
    settings = result["engine_settings"]
    if engine in _TEXT_ENGINES and isinstance(settings, dict):
        result["engine_settings"] = {"normalize_lookalikes": False, **settings}
    result["detail"] = _sentence(result["detail"])
    return result


def _spoiled_by_the_judge_bug(results: dict) -> bool:
    return any(isinstance(r, dict) and r.get("engine") == "llm_judge"
               and r.get("detail") == _JUDGE_BUG for r in results.values())


def upgrade() -> None:
    connection = op.get_bind()
    deterministic_types = {
        name for name, category in connection.execute(
            sa.select(_test_types.c.name, _test_types.c.category))
        if category == "deterministic"
    }

    for run_id, error, results in connection.execute(
            sa.select(_test_runs.c.id, _test_runs.c.error, _test_runs.c.results)).all():
        if not isinstance(results, dict):
            results = None  # no results yet (Pending, Running, NotRan)
        if results and _spoiled_by_the_judge_bug(results):
            connection.execute(_test_runs.update().where(_test_runs.c.id == run_id).values(
                # sa.null(): a JSON column given None stores the JSON text null,
                # and a run that hasn't executed has SQL NULL there
                status="pending", error=None, results=sa.null(), executed_at=None,
                evaluated_output=None, output_source=None, application_reply=sa.null(),
            ))
            continue
        values = {}
        corrected_error = _message(error)
        if corrected_error != error:
            values["error"] = corrected_error
        if results:
            corrected = {
                _RENAMED.get(name, name): (
                    _result(_RENAMED.get(name, name), result, deterministic_types)
                    if isinstance(result, dict) else result
                )
                for name, result in results.items()
            }
            if corrected != results:
                values["results"] = corrected
        if values:
            connection.execute(
                _test_runs.update().where(_test_runs.c.id == run_id).values(**values))

    for table_name in ("target_checks", "judge_checks"):
        checks = sa.table(table_name, sa.column("id", sa.Uuid()), sa.column("error", sa.Text()))
        for check_id, error in connection.execute(
                sa.select(checks.c.id, checks.c.error).where(checks.c.error.is_not(None))).all():
            corrected = _message(error)
            if corrected != error:
                connection.execute(
                    checks.update().where(checks.c.id == check_id).values(error=corrected))


def downgrade() -> None:
    # Nothing to undo: the corrected values replace wrong ones, not an
    # alternative representation.
    pass
