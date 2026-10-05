# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""word the statistical tests' parameter hints without a unit

Revision ID: 9c4d2e7f0a1b
Revises: 7a3c5e9f1b2d
Create Date: 2026-10-02 11:00:00.000000

The hints quoted the API's unit — "0.9 means at least 90% of the time",
"at 0.95 a proven claim is wrong at most 1 time in 20" — while the dialog
shows the same parameter as a percentage, so the sentence under a "90 %"
field spoke of 0.9. The parameter's `kind` (`rate`, `level`, `share`: a
share of 1) already tells a client the unit; the hint now explains the
meaning in the spoken form and quotes no literal. A hint an operator has
already reworded is left alone, both ways.

"""
import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '9c4d2e7f0a1b'
down_revision: str | None = '7a3c5e9f1b2d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONFIDENCE = (
    "How sure a verdict must be: at 0.95 a proven claim is wrong at most 1 time in 20. Higher "
    "needs more runs.",
    "How sure a verdict must be: at 95% a proven claim is wrong at most 1 time in 20. Higher "
    "needs more runs.",
)

# (row id, parameter key) → (the seeded hint, the unit-neutral one); the
# confidence hint is shared by every row
_HINTS: dict[tuple[str | None, str], tuple[str, str]] = {
    (None, "confidence"): _CONFIDENCE,
    ("binomial_gate", "target"): (
        "The share of runs each check must pass: 0.9 means \"at least 90% of the time\". "
        "Higher targets need many more runs.",
        "The share of runs each check must pass — \"at least 90% of the time\", say. Higher "
        "targets need many more runs.",
    ),
    ("judge_stability", "target"): (
        "The share of runs that must agree with the judge's usual verdict (pass or fail, "
        "whichever it gives more often): 0.9 means it changes its mind at most 1 time in 10.",
        "The share of runs that must agree with the judge's usual verdict (pass or fail, "
        "whichever it gives more often): at 90% agreement it changes its mind at most 1 time "
        "in 10.",
    ),
    ("no_worse", "margin"): (
        "How much worse B may be and still count as no worse: 0.05 is 5 points of pass rate. "
        "Smaller margins need many more runs.",
        "How much worse B may be and still count as no worse — 5 points of pass rate, say. "
        "Smaller margins need many more runs.",
    ),
    ("one_sample_t", "difference"): (
        "How far from the threshold an average must be for you to care, as a share of the "
        "check's score range (0.05 = 0.05 on ROUGE, 5 points on BLEU). Only sizes the "
        "suggestion.",
        "How far from the threshold an average must be for you to care, as a share of the "
        "check's score range (5% of the range is 0.05 on ROUGE, 5 points on BLEU). Only "
        "sizes the suggestion.",
    ),
}

_statistical_tests = sa.table(
    "statistical_tests",
    sa.column("id", sa.Text()),
    sa.column("parameters", sa.JSON()),
)


def _reword(backwards: bool) -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.select(_statistical_tests.c.id,
                                        _statistical_tests.c.parameters)).all()
    for row_id, parameters in rows:
        parameters = json.loads(parameters) if isinstance(parameters, str) else parameters
        changed = False
        for parameter in parameters or []:
            wording = _HINTS.get((row_id, parameter.get("key"))) or _HINTS.get(
                (None, parameter.get("key")))
            if wording is None:
                continue
            before, after = (wording[1], wording[0]) if backwards else wording
            if parameter.get("hint") == before:
                parameter["hint"] = after
                changed = True
        if changed:
            connection.execute(
                sa.update(_statistical_tests)
                .where(_statistical_tests.c.id == row_id)
                .values(parameters=parameters))


def upgrade() -> None:
    _reword(backwards=False)


def downgrade() -> None:
    _reword(backwards=True)
