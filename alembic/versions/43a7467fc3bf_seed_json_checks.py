# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""seed the JSON checks: Is Valid JSON, Matches JSON Schema, JSON Field Equals

Revision ID: 43a7467fc3bf
Revises: 5e3f74c874ae
Create Date: 2026-09-30 10:00:00.000000

Three catalogue rows on the new `json` engine, one per `check` it runs
after parsing the answer: `valid`, `schema` and `field`. All read the JSON
out of a Markdown code fence when the whole answer is one
(`strip_fences`). Deterministic, very fast, no comparison. The downgrade
deletes them by name; it fails, as it should, while a test still has one
assigned.

"""
import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = '43a7467fc3bf'
down_revision: str | None = '5e3f74c874ae'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROWS = [
    {
        "name": "Is Valid JSON",
        "description": "Checks that the output is valid JSON, reading it from inside a "
                       "```json code fence when the whole answer is one.",
        "best_for": "Applications that must return machine-readable output: tool calls, "
                    "extraction, form filling.",
        "limitations": "Says nothing about the shape or the values; pair it with Matches "
                       "JSON Schema or JSON Field Equals.",
        "config_fields": [],
        "engine_settings": {"check": "valid", "strip_fences": True},
    },
    {
        "name": "Matches JSON Schema",
        "description": "Checks that the output is valid JSON and satisfies a JSON Schema: "
                       "required fields, types, allowed values.",
        "best_for": "Structured outputs with a fixed contract: API responses, extracted "
                    "records, tool-call arguments.",
        "limitations": "Checks the structure, not whether the values are right; writing the "
                       "schema takes some effort.",
        "config_fields": [{"key": "schema", "label": "JSON Schema", "kind": "multiline",
                           "required": True}],
        "engine_settings": {"check": "schema", "strip_fences": True},
    },
    {
        "name": "JSON Field Equals",
        "description": "Checks that the output is valid JSON with an expected value at a "
                       "JSONPath, compared type for type.",
        "best_for": "One decisive field in a structured answer: a status, a category, a "
                    "flag, an amount.",
        "limitations": "Checks one value — the first match at the path; several fields need "
                       "several checks or a schema.",
        "config_fields": [
            {"key": "path", "label": "JSONPath", "kind": "multiline", "required": True},
            {"key": "value", "label": "Expected value (JSON)", "kind": "multiline",
             "required": True},
        ],
        "engine_settings": {"check": "field", "strip_fences": True},
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
                "engine": "json",
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
