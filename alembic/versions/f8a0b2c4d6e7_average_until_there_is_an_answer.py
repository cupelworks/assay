"""the average, until there's an answer: a catalogue row

Revision ID: f8a0b2c4d6e7
Revises: e7f9a1b3c5d6
Create Date: 2026-10-03 18:00:00.000000

"Scores high enough on average — until there's an answer" (engine
sequential_t): the one-sample t-test run in waves up to a maximum, stopping
as soon as every scored check's average has its answer. Its first wave is
the t-test's floor (10 scores); each look's level is Pocock's constant for
the batch's looks, so a wrong answer over all of them stays within the
confidence for normal scores (approximately for few). The batch columns
for waves exist already (e7f9a1b3c5d6).

"""
from collections.abc import Sequence
from datetime import datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = 'f8a0b2c4d6e7'
down_revision: str | None = 'e7f9a1b3c5d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROW = {
    "id": "sequential_t",
    "name": "Scores high enough on average — until there's an answer",
    "engine": "sequential_t",
    "question": "Is each scored check's average on the right side of its threshold? Set the "
                "most you're willing to run; it runs in waves and stops as soon as every "
                "average has its answer, so a clear case costs little.",
    "parameters": [
        {"key": "confidence", "label": "How sure", "kind": "level", "default": 0.95,
         "min": 0.80, "max": 0.999,
         "hint": "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                 "runs."},
    ],
    "engine_settings": {"floor": 10},
    "floor_explanation":
        "An average of fewer than 10 scores is too unreliable to judge, so the first wave is "
        "10 runs. It looks at the average after each wave, a little more strictly than a "
        "single test, and stops as soon as it's clear.",
    "floor_examples": [],
    "method": "A group-sequential one-sample t-test: Student's t at each wave's end, at one "
              "stricter level — Pocock's constant for the batch's own looks, found by "
              "integrating the normal score process across them — so the chance of a wrong "
              "answer overall stays within the confidence (exactly for normal scores, "
              "approximately for few); an average is answered at the first look that decides "
              "it.",
}

_tests = sa.table(
    "statistical_tests",
    sa.column("id", sa.Text()), sa.column("name", sa.Text()), sa.column("engine", sa.Text()),
    sa.column("question", sa.Text()), sa.column("parameters", sa.JSON()),
    sa.column("engine_settings", sa.JSON()), sa.column("floor_explanation", sa.Text()),
    sa.column("floor_examples", sa.JSON()), sa.column("method", sa.Text()),
    sa.column("created_at", sa.DateTime()),
)


def upgrade() -> None:
    newest = op.get_bind().execute(sa.select(sa.func.max(_tests.c.created_at))).scalar()
    if isinstance(newest, str):
        newest = datetime.fromisoformat(newest)
    op.bulk_insert(_tests, [{**_ROW, "created_at": (newest or datetime.now())
                             + timedelta(seconds=1)}])


def downgrade() -> None:
    op.execute("DELETE FROM statistical_tests WHERE id = 'sequential_t'")
