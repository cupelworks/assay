# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""seed the length limits: Word Count Limit, Character Count Limit

Revision ID: fc7d91b00c18
Revises: 43a7467fc3bf
Create Date: 2026-09-30 12:00:00.000000

Two catalogue rows on the new `length` engine, one per `unit`. Each takes a
required maximum and an optional minimum per assignment, both inclusive
whole numbers — their `min`/`max` descriptor bounds keep the FE's input at
0 or more, with no upper bound. Deterministic, very fast, no comparison.
The downgrade deletes them by name; it fails, as it should, while a test
still has one assigned.

"""
import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = 'fc7d91b00c18'
down_revision: str | None = '43a7467fc3bf'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _bounds(unit: str) -> list[dict]:
    return [
        {"key": "max", "label": f"Maximum {unit}", "kind": "numeric", "required": True,
         "min": 0.0, "max": None},
        {"key": "min", "label": f"Minimum {unit}", "kind": "numeric", "required": False,
         "min": 0.0, "max": None},
    ]


_ROWS = [
    {
        "name": "Word Count Limit",
        "description": "Checks that the output has at most a maximum number of words, and "
                       "optionally at least a minimum.",
        "best_for": "Length requirements on prose: summaries, descriptions, short answers "
                    "that must fit a card or a screen.",
        "limitations": "Words are whitespace-separated, so a hyphenated term or a number "
                       "counts as one; says nothing about what the words say.",
        "config_fields": _bounds("words"),
        "engine_settings": {"unit": "words"},
    },
    {
        "name": "Character Count Limit",
        "description": "Checks that the output has at most a maximum number of characters, "
                       "and optionally at least a minimum, ignoring whitespace at both ends.",
        "best_for": "Hard size limits: SMS replies, push notifications, titles, form fields.",
        "limitations": "Counts characters as Unicode code points, so some emoji and "
                       "accented letters count as more than one.",
        "config_fields": _bounds("characters"),
        "engine_settings": {"unit": "characters"},
    },
]


def upgrade() -> None:
    now = datetime.now().astimezone()
    op.bulk_insert(
        sa.table(
            "test_types",
            sa.column("id", sa.Uuid()),
            sa.column("name", sa.Text()),
            sa.column("category", sa.Text()),
            sa.column("description", sa.Text()),
            sa.column("best_for", sa.Text()),
            sa.column("cost", sa.Text()),
            sa.column("limitations", sa.Text()),
            sa.column("config_fields", sa.JSON()),
            sa.column("engine", sa.Text()),
            sa.column("engine_settings", sa.JSON()),
            sa.column("comparison", sa.Text()),
            sa.column("is_active", sa.Boolean()),
            sa.column("created_at", sa.DateTime()),
        ),
        [
            {
                "id": uuid.uuid4(),
                "category": "deterministic",
                "cost": "very_fast",
                "engine": "length",
                "comparison": None,
                "is_active": True,
                "created_at": now,
                **row,
            }
            for row in _ROWS
        ],
    )


def downgrade() -> None:
    names = [row["name"] for row in _ROWS]
    op.execute(
        sa.table("test_types", sa.column("name", sa.Text()))
        .delete()
        .where(sa.column("name").in_(names))
    )
