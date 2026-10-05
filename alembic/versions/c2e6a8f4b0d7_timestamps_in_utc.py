# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""every timestamp stored in UTC

Revision ID: c2e6a8f4b0d7
Revises: b8d4f2a6c1e3
Create Date: 2026-10-04 14:30:00.000000

Until now every timestamp was stored as the server's local time, without an
offset, so a stored moment changed meaning when daylight saving ended or the
server moved to another timezone. From here every timestamp is stored in UTC,
still without an offset. This converts the stored ones: each is read as the
local time of the machine running the migration (the server that wrote it),
with that date's offset, so summer and winter values each get their own. The
hour repeated when the clocks go back can't be told apart afterwards; such a
value is read as its first occurrence.

The downgrade converts back to the machine's local time.

Timestamps inside stored JSON (a batch result's `computed_at`) carry their
offset already and are left as they are.
"""
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = 'c2e6a8f4b0d7'
down_revision: str | None = 'b8d4f2a6c1e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# every timestamp column at this revision, with its table's key
TIMESTAMPS = {
    "datasets": ("id", ["created_at"]),
    "judge_checks": ("id", ["created_at", "completed_at"]),
    "settings": ("section", ["updated_at"]),
    "standalone_runs": ("id", ["snapshot_at"]),
    "statistical_batches": ("id", ["waves_closed_at", "created_at", "stopped_at",
                                   "completed_at"]),
    "statistical_comparisons": ("id", ["created_at"]),
    "statistical_tests": ("id", ["created_at"]),
    "target_checks": ("id", ["created_at", "completed_at"]),
    "test_plan_executions": ("id", ["created_at"]),
    "test_plans": ("id", ["created_at"]),
    "test_runs": ("id", ["created_at", "executed_at"]),
    "test_set_entries": ("id", ["snapshot_at"]),
    "test_set_executions": ("id", ["created_at"]),
    "test_sets": ("id", ["created_at"]),
    "test_types": ("id", ["created_at"]),
    "tests": ("id", ["created_at"]),
}


def _local_to_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _utc_to_local(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC).astimezone().replace(tzinfo=None)


def _convert(change: Callable[[datetime], datetime]) -> None:
    connection = op.get_bind()
    for name, (key, columns) in TIMESTAMPS.items():
        table = sa.table(name, sa.column(key), *(sa.column(c, sa.DateTime) for c in columns))
        rows = connection.execute(sa.select(table.c[key], *(table.c[c] for c in columns)))
        for row in rows.all():
            values = {c: change(row[i + 1]) for i, c in enumerate(columns)
                      if row[i + 1] is not None}
            if values:
                connection.execute(table.update().where(table.c[key] == row[0]).values(**values))


def upgrade() -> None:
    _convert(_local_to_utc)


def downgrade() -> None:
    _convert(_utc_to_local)
