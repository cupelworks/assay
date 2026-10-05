# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""a batch's per-check targets and left-out checks; runs that skip checks

Revision ID: c5d7e9a1b3f4
Revises: b2e8f4a6c0d3
Create Date: 2026-10-03 12:00:00.000000

A batch can set one check's own target ("Relevance at 8 in 10") and leave
checks out (docs/version_1/statistics/dev_notes.md note 32): `statistical_batches.
overrides` keeps them as the request gave them, `{"targets": [{"entry_id",
"label", "target"}], "leave_out": [{"entry_id", "label"}]}`, null for a
batch with neither. A left-out check isn't evaluated at all, so its judge
calls are saved: `test_runs.skip_labels` lists the labels a run skips, null
for every ordinary run. Both columns start null everywhere.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c5d7e9a1b3f4'
down_revision: str | None = 'b2e8f4a6c0d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("statistical_batches") as batch:
        batch.add_column(sa.Column("overrides", sa.JSON(), nullable=True))
    with op.batch_alter_table("test_runs") as batch:
        batch.add_column(sa.Column("skip_labels", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("test_runs") as batch:
        batch.drop_column("skip_labels")
    with op.batch_alter_table("statistical_batches") as batch:
        batch.drop_column("overrides")
