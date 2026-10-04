"""run until there's an answer: two catalogue rows, and a batch's waves

Revision ID: e7f9a1b3c5d6
Revises: d6e8f0a2c4b5
Create Date: 2026-10-03 15:00:00.000000

A batch can run in waves and stop as soon as every check has an answer
(docs/version_1/statistics/dev_notes.md notes 30-32): two catalogue rows on two new
engines, "Passes reliably — until there's an answer" (sequential_gate) and
"The judge is consistent — until there's an answer"
(sequential_judge_stability), with the same parameters as their fixed
counterparts. A batch on them keeps how many waves it has released
(`waves_released`) and when it stopped releasing them (`waves_closed_at`:
every check answered, the maximum reached, or a hand Stop); both stay null
for every other batch.

"""
from collections.abc import Sequence
from datetime import datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = 'e7f9a1b3c5d6'
down_revision: str | None = 'd6e8f0a2c4b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HOW_SURE = {"key": "confidence", "label": "How sure", "kind": "level", "default": 0.95,
             "min": 0.80, "max": 0.999,
             "hint": "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                     "runs."}

_ROWS = [
    {
        "id": "sequential_gate",
        "name": "Passes reliably — until there's an answer",
        "engine": "sequential_gate",
        "question": "Does each check pass almost every time? Set the most you're willing to "
                    "run; it runs in waves and stops as soon as every check has an answer, "
                    "so a clearly good or clearly bad check costs little.",
        "parameters": [
            {"key": "target", "label": "How often it must pass", "kind": "rate",
             "default": 0.9, "min": 0.5, "max": 0.999,
             "hint": "The share of runs each check must pass — \"at least 9 times in 10\", "
                     "say. The higher, the more runs it takes."},
            _HOW_SURE,
        ],
        "engine_settings": {},
        "floor_explanation":
            "It looks at the results after each wave, so each look is a little stricter than "
            "a single test of the same size: a perfect record answers at the first look "
            "(about 40 runs at 9 in 10, 95% sure) instead of 29. In exchange it never needs "
            "you to guess the right size.",
        "floor_examples": [],
        "method": "A group-sequential exact binomial test: the fixed gate's test at each "
                  "wave's end, at one stricter level calibrated exactly (by the binomial, "
                  "over the batch's own looks) so the chance of a wrong answer overall stays "
                  "within the confidence level; a check is answered at the first look that "
                  "decides it, and the batch stops when every check is answered or the "
                  "maximum is reached.",
    },
    {
        "id": "sequential_judge_stability",
        "name": "The judge is consistent — until there's an answer",
        "engine": "sequential_judge_stability",
        "question": "Does the LLM judge give the same verdict when it sees the same answer "
                    "again? It runs in waves and stops as soon as every judge check has an "
                    "answer.",
        "parameters": [
            {"key": "target", "label": "How often it must agree", "kind": "rate",
             "default": 0.9, "min": 0.6, "max": 0.999,
             "hint": "The share of runs that must give the judge's usual verdict (pass or "
                     "fail, whichever it gives more often) — \"at least 9 times in 10\", say."},
            _HOW_SURE,
        ],
        "engine_settings": {},
        "floor_explanation":
            "Like \"The judge is consistent\", looked at after each wave: each look is a "
            "little stricter, so a judge that always agrees answers at the first look. Only "
            "judge checks on a recorded answer count.",
        "floor_examples": [],
        "method": "The group-sequential exact binomial test of \"Passes reliably — until "
                  "there's an answer\", on the agreement count — the runs that gave the "
                  "judge's usual verdict.",
    },
]

_tests = sa.table(
    "statistical_tests",
    sa.column("id", sa.Text()), sa.column("name", sa.Text()), sa.column("engine", sa.Text()),
    sa.column("question", sa.Text()), sa.column("parameters", sa.JSON()),
    sa.column("engine_settings", sa.JSON()), sa.column("floor_explanation", sa.Text()),
    sa.column("floor_examples", sa.JSON()), sa.column("method", sa.Text()),
    sa.column("created_at", sa.DateTime()),
)


def upgrade() -> None:
    with op.batch_alter_table("statistical_batches") as batch:
        batch.add_column(sa.Column("waves_released", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("waves_closed_at", sa.DateTime(), nullable=True))
    newest = op.get_bind().execute(sa.select(sa.func.max(_tests.c.created_at))).scalar()
    if isinstance(newest, str):
        newest = datetime.fromisoformat(newest)
    stamp = newest or datetime.now()
    op.bulk_insert(_tests, [{**row, "created_at": stamp + timedelta(seconds=i + 1)}
                            for i, row in enumerate(_ROWS)])


def downgrade() -> None:
    op.execute("DELETE FROM statistical_tests WHERE id IN "
               "('sequential_gate', 'sequential_judge_stability')")
    with op.batch_alter_table("statistical_batches") as batch:
        batch.drop_column("waves_closed_at")
        batch.drop_column("waves_released")
