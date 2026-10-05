# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""add the judge settings group and judge_checks, and ready the judge rows

Revision ID: 1e4b81f30147
Revises: 1b6c140a6035
Create Date: 2026-10-01 05:00:00.000000

The LLM judge's settings are a second group in the settings table: its
`section` enum gains `judge` (a CHECK constraint on SQLite, a native enum
type on PostgreSQL). `judge_checks` holds checks of those settings, the
same shape and lifecycle as `target_checks` minus the `input` — a judge
check always asks the same fixed question.

The five judge rows gain `engine_settings.reference` — whether the judge
sees the expected output: Correctness and Hallucination (which require
one), not Relevance, Bias or Toxicity. Their texts say in plain words what
each asks the judge, and each `rubric` field gets the row's default rubric
as its placeholder and a hint. The downgrade restores the rows, drops the
table and the saved judge settings, and narrows the enum back on SQLite;
PostgreSQL can't drop an enum value, so `judge` stays in its type there.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '1e4b81f30147'
down_revision: str | None = '1b6c140a6035'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USES_REFERENCE = {"Correctness", "Hallucination"}
_COST_AND_SETUP = ("Each check is one call to the judge model chosen under Settings, which "
                   "takes a few seconds and costs money. An AI judge can be inconsistent on "
                   "borderline cases.")
_TEXTS = {
    "Correctness": {
        "description": "Asks an AI judge whether the answer states the same facts and "
                       "conclusions as the expected text, however it's worded.",
        "best_for": "Open questions whose right answer can be worded many ways.",
        "limitations": f"The judge compares with the expected text only. {_COST_AND_SETUP}",
    },
    "Relevance": {
        "description": "Asks an AI judge whether the answer addresses the question that was "
                       "actually asked, rather than something else or mostly filler.",
        "best_for": "Chatbots and assistants that must stay on the user's question.",
        "limitations": f"Says nothing about whether the answer is right. {_COST_AND_SETUP}",
    },
    "Bias": {
        "description": "Asks an AI judge whether the answer treats any person or group "
                       "unfairly, stereotypes or shows prejudice. Passes when none is found.",
        "best_for": "Applications serving the public or diverse groups of people.",
        "limitations": f"The judge model can carry biases of its own. {_COST_AND_SETUP}",
    },
    "Toxicity": {
        "description": "Asks an AI judge whether the answer contains insults, harassment, "
                       "hate, threats or demeaning language. Passes when none is found.",
        "best_for": "Customer-facing chatbots and anything the public reads.",
        "limitations": f"Can miss subtle cases or flag harmless ones. {_COST_AND_SETUP}",
    },
    "Hallucination": {
        "description": "Asks an AI judge whether the answer claims anything the expected text "
                       "doesn't support — invented facts, figures, names or sources. Passes "
                       "when nothing is invented.",
        "best_for": "Answers that must stick to known facts, such as RAG and support answers.",
        "limitations": "The judge checks against the expected text only: a true fact the "
                       f"expected text doesn't mention counts as unsupported. {_COST_AND_SETUP}",
    },
}
_RUBRIC_HINT = ("Optional. Replaces the default rubric, shown as the example; say what the "
                "answer must do to pass.")

_test_types = sa.table(
    "test_types",
    sa.column("name", sa.Text()),
    sa.column("engine", sa.Text()),
    sa.column("description", sa.Text()),
    sa.column("best_for", sa.Text()),
    sa.column("limitations", sa.Text()),
    sa.column("engine_settings", sa.JSON()),
    sa.column("config_fields", sa.JSON()),
)
_settings = sa.table("settings", sa.column("section", sa.Text()))


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE settingssection ADD VALUE IF NOT EXISTS 'judge'")
    else:
        _section_values("target", "judge")

    op.create_table(
        'judge_checks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.Enum('pending', 'running', 'completed', name='judgecheckstatus',
                                    create_constraint=True), nullable=False),
        sa.Column('settings', sa.JSON(), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Float(), nullable=True),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    for name, settings, fields in _judge_rows():
        fields = [
            {**field, "placeholder": settings["default_rubric"], "hint": _RUBRIC_HINT}
            if field["key"] == "rubric" else field
            for field in fields
        ]
        op.execute(_test_types.update().where(_test_types.c.name == name).values(
            engine_settings={**settings, "reference": name in _USES_REFERENCE},
            config_fields=fields,
            **_TEXTS[name],
        ))


def downgrade() -> None:
    for name, settings, fields in _judge_rows():
        fields = [
            {key: value for key, value in field.items() if key not in ("placeholder", "hint")}
            if field["key"] == "rubric" else field
            for field in fields
        ]
        restored = {key: value for key, value in settings.items() if key != "reference"}
        op.execute(_test_types.update().where(_test_types.c.name == name).values(
            engine_settings=restored, config_fields=fields, **_BEFORE[name],
        ))

    op.drop_table('judge_checks')
    op.execute(_settings.delete().where(_settings.c.section == "judge"))
    if op.get_bind().dialect.name != "postgresql":
        _section_values("target")


def _judge_rows() -> list[tuple[str, dict, list[dict]]]:
    rows = op.get_bind().execute(
        sa.select(_test_types.c.name, _test_types.c.engine_settings,
                  _test_types.c.config_fields)
        .where(_test_types.c.engine == "llm_judge")
    ).all()
    return [(name, settings, fields) for name, settings, fields in rows if name in _TEXTS]


def _section_values(*values: str) -> None:
    """Recreate the settings table with `section` constrained to values."""
    with op.batch_alter_table('settings', recreate='always') as batch_op:
        batch_op.alter_column(
            'section',
            existing_type=sa.Enum('target', 'judge', name='settingssection',
                                  create_constraint=True),
            type_=sa.Enum(*values, name='settingssection', create_constraint=True),
            existing_nullable=False,
        )


_BEFORE = {
    "Correctness": {
        "description": "Uses an LLM to evaluate factual correctness of the output.",
        "best_for": "Open-ended Q&A where exact match is too strict.",
        "limitations": "LLM judgments can be inconsistent; prone to positivity bias.",
    },
    "Relevance": {
        "description": "Uses an LLM to evaluate whether the output is relevant to the input.",
        "best_for": "RAG pipelines and chatbot responses.",
        "limitations": "Subjective; different LLMs may disagree on what counts as relevant.",
    },
    "Bias": {
        "description": "Uses an LLM to detect bias or unfair treatment in the output.",
        "best_for": "Applications serving diverse user groups.",
        "limitations": "The judge LLM may itself carry biases that affect evaluation.",
    },
    "Toxicity": {
        "description": "Uses an LLM to detect harmful or toxic content in the output.",
        "best_for": "Customer-facing applications and public-facing chatbots.",
        "limitations": "May miss subtle toxicity or flag false positives in edge cases.",
    },
    "Hallucination": {
        "description": "Uses an LLM to detect fabricated or unsupported claims in the output.",
        "best_for": "RAG pipelines and factual question answering.",
        "limitations": "The judge LLM may itself hallucinate when verifying facts.",
    },
}
