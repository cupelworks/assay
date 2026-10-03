"""the trial: a statistical test with no verdict, and the status Done

Revision ID: d6e8f0a2c4b5
Revises: c5d7e9a1b3f4
Create Date: 2026-10-03 13:00:00.000000

A check that has never run gives the estimate nothing to plan from
(docs/statistics/dev_notes.md note 32): a trial runs the scope a few times
to learn how each check behaves, and gives no verdict. It's a catalogue row
on its own engine, `trial` ("Learn how it behaves": 10 times by default, 50
at most), and a finished trial's status is a new word, `Done` — it asked no
question, so Inconclusive would mislead. The status constraint gains it.

The downgrade removes the row; it fails, as it should, while a batch still
has the status Done.

"""
from collections.abc import Sequence
from datetime import datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = 'd6e8f0a2c4b5'
down_revision: str | None = 'c5d7e9a1b3f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = ("pending", "running", "passed", "failed", "inconclusive", "incomplete", "not_ran")
_AFTER = (*_BEFORE, "done")

_TRIAL = {
    "id": "trial",
    "name": "Learn how it behaves",
    "engine": "trial",
    "question": "How does each check behave when it runs again and again? A few runs with "
                "no verdict, so the next batch can be planned from what they show.",
    "parameters": [],
    "engine_settings": {"default_times": 10, "max_times": 50},
    "floor_explanation": "No minimum: a trial answers no question. 10 times is enough to "
                         "see roughly how often each check passes; every run also makes the "
                         "next estimate more precise.",
    "floor_examples": [],
    "method": "No statistical test: each check's pass rate over the trial's runs, with its "
              "Wilson range. The runs become the history the next estimate plans from.",
}

_tests = sa.table(
    "statistical_tests",
    sa.column("id", sa.Text()), sa.column("name", sa.Text()), sa.column("engine", sa.Text()),
    sa.column("question", sa.Text()), sa.column("parameters", sa.JSON()),
    sa.column("engine_settings", sa.JSON()), sa.column("floor_explanation", sa.Text()),
    sa.column("floor_examples", sa.JSON()), sa.column("method", sa.Text()),
    sa.column("created_at", sa.DateTime()),
)


def _statuses(values: tuple[str, ...], old: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name == "postgresql":
        if "done" in values and "done" not in old:
            op.execute("ALTER TYPE batchstatus ADD VALUE IF NOT EXISTS 'done'")
        return  # a value can't be dropped from a Postgres enum type; harmless to keep
    with op.batch_alter_table("statistical_batches") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.Enum(*old, name="batchstatus", create_constraint=True),
            type_=sa.Enum(*values, name="batchstatus", create_constraint=True),
            existing_nullable=False)


def upgrade() -> None:
    _statuses(_AFTER, _BEFORE)
    # a second after the newest row: it lists last among the batch tests
    newest = op.get_bind().execute(sa.select(sa.func.max(_tests.c.created_at))).scalar()
    if isinstance(newest, str):
        newest = datetime.fromisoformat(newest)
    stamp = (newest or datetime.now()) + timedelta(seconds=1)
    op.bulk_insert(_tests, [{**_TRIAL, "created_at": stamp}])


def downgrade() -> None:
    op.execute("DELETE FROM statistical_tests WHERE id = 'trial'")
    _statuses(_BEFORE, _AFTER)
