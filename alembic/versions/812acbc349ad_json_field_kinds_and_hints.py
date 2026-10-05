# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""give the JSON and length fields their kinds, placeholders and hints

Revision ID: 812acbc349ad
Revises: fc7d91b00c18
Create Date: 2026-09-30 14:00:00.000000

The JSON checks' `schema` and `value` fields become kind `json`, and
JSON Field Equals' `path` kind `jsonpath`, so the FE can edit them as such
and the API checks they parse when the type is assigned. Those fields and
the length limits' bounds also get a `placeholder` (an example value) and
a `hint` (a one-line note). Every other row is untouched; a field without
them reads back with both null. The downgrade restores the fields as they
were seeded.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '812acbc349ad'
down_revision: str | None = 'fc7d91b00c18'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BOUND_HINT = "A whole number; an answer of exactly this length meets it."
_OPTIONAL_HINT = "Optional. A whole number; leave it empty for no minimum."

_NEW = {
    "Matches JSON Schema": [
        {"key": "schema", "label": "JSON Schema", "kind": "json", "required": True,
         "placeholder": '{"type": "object", "required": ["status"]}',
         "hint": "A JSON Schema, written as JSON: the structure the answer must have."},
    ],
    "JSON Field Equals": [
        {"key": "path", "label": "JSONPath", "kind": "jsonpath", "required": True,
         "placeholder": "$.status",
         "hint": "Where the value is in the answer, e.g. $.items[0].sku."},
        {"key": "value", "label": "Expected value (JSON)", "kind": "json", "required": True,
         "placeholder": '"approved"',
         "hint": 'A JSON value: strings in double quotes, e.g. "approved"; 42, true and '
                 "null as they are."},
    ],
    "Word Count Limit": [
        {"key": "max", "label": "Maximum words", "kind": "numeric", "required": True,
         "min": 0.0, "max": None, "placeholder": "100", "hint": _BOUND_HINT},
        {"key": "min", "label": "Minimum words", "kind": "numeric", "required": False,
         "min": 0.0, "max": None, "placeholder": None, "hint": _OPTIONAL_HINT},
    ],
    "Character Count Limit": [
        {"key": "max", "label": "Maximum characters", "kind": "numeric", "required": True,
         "min": 0.0, "max": None, "placeholder": "160",
         "hint": "A whole number; spaces and newlines at the start or end aren't counted."},
        {"key": "min", "label": "Minimum characters", "kind": "numeric", "required": False,
         "min": 0.0, "max": None, "placeholder": None, "hint": _OPTIONAL_HINT},
    ],
}

_OLD = {
    "Matches JSON Schema": [
        {"key": "schema", "label": "JSON Schema", "kind": "multiline", "required": True},
    ],
    "JSON Field Equals": [
        {"key": "path", "label": "JSONPath", "kind": "multiline", "required": True},
        {"key": "value", "label": "Expected value (JSON)", "kind": "multiline",
         "required": True},
    ],
    "Word Count Limit": [
        {"key": "max", "label": "Maximum words", "kind": "numeric", "required": True,
         "min": 0.0, "max": None},
        {"key": "min", "label": "Minimum words", "kind": "numeric", "required": False,
         "min": 0.0, "max": None},
    ],
    "Character Count Limit": [
        {"key": "max", "label": "Maximum characters", "kind": "numeric", "required": True,
         "min": 0.0, "max": None},
        {"key": "min", "label": "Minimum characters", "kind": "numeric", "required": False,
         "min": 0.0, "max": None},
    ],
}

_test_types = sa.table(
    "test_types",
    sa.column("name", sa.Text()),
    sa.column("config_fields", sa.JSON()),
)


def _set(config_fields_by_name: dict[str, list[dict]]) -> None:
    for name, config_fields in config_fields_by_name.items():
        op.execute(
            _test_types.update()
            .where(_test_types.c.name == name)
            .values(config_fields=config_fields)
        )


def upgrade() -> None:
    _set(_NEW)


def downgrade() -> None:
    _set(_OLD)
