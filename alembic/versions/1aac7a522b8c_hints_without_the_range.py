# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""drop the "0 to 1:" range from the ROUGE threshold hints

Revision ID: 1aac7a522b8c
Revises: 954995a8255b
Create Date: 2026-09-30 20:00:00.000000

The FE already shows a numeric field's range and pass rule on its label,
from the descriptor's `min`/`max` and the row's `comparison`, so a hint that
starts with the range repeats it. A hint explains what the value means and
what to expect; the five ROUGE threshold hints now start there. The
downgrade puts the range back.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '1aac7a522b8c'
down_revision: str | None = '954995a8255b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROWS = ("ROUGE", "ROUGE-1", "ROUGE-2", "ROUGE-L Recall", "ROUGE-L Precision")
_RANGE = "0 to 1: "

_test_types = sa.table(
    "test_types",
    sa.column("name", sa.Text()),
    sa.column("config_fields", sa.JSON()),
)


def _rewrite_threshold_hints(rewrite) -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(_test_types.c.name, _test_types.c.config_fields)
        .where(_test_types.c.name.in_(_ROWS))
    ).all()
    for name, config_fields in rows:
        fields = [
            {**field, "hint": rewrite(field["hint"])}
            if field["key"] == "threshold" and field.get("hint") else field
            for field in config_fields
        ]
        connection.execute(_test_types.update().where(_test_types.c.name == name)
                           .values(config_fields=fields))


def upgrade() -> None:
    def without_range(hint: str) -> str:
        explanation = hint.removeprefix(_RANGE)
        return explanation[:1].upper() + explanation[1:]
    _rewrite_threshold_hints(without_range)


def downgrade() -> None:
    def with_range(hint: str) -> str:
        return _RANGE + hint[:1].lower() + hint[1:]
    _rewrite_threshold_hints(with_range)
