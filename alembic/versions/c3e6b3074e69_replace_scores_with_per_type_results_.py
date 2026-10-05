# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""replace scores with per-type results and rework test run status

Revision ID: c3e6b3074e69
Revises: c7891554368a
Create Date: 2026-09-24 14:37:13.776095

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3e6b3074e69'
down_revision: Union[str, None] = 'c7891554368a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# SQLAlchemy's Enum type persists the Python enum member's *name*
# (lowercase: "pending", "completed", ...), not its StrEnum .value
# ("Pending", "Completed", ...) - these UPDATEs match storage, not display.
test_runs_status = sa.table("test_runs", sa.column("status", sa.Text()))


def upgrade() -> None:
    # Dev DB has 993 pre-existing rows using the old "completed"/"failed"
    # status values - scores and error are null for all of them (no real
    # per-type result ever backed these; they predate execution being
    # built), so there's nothing to carry into `results`. Remapped to the
    # closest new equivalent rather than left as now-invalid values:
    # completed -> green (old model's best equivalent of "ran
    # successfully"), failed -> not_ran (a run-level failure, same pairing
    # "failed" already implied, just with no error message to carry over
    # since none was ever set on these rows).
    op.execute(
        test_runs_status.update()
        .where(test_runs_status.c.status == "completed")
        .values(status="green")
    )
    op.execute(
        test_runs_status.update()
        .where(test_runs_status.c.status == "failed")
        .values(status="not_ran")
    )

    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('results', sa.JSON(), nullable=True))
        batch_op.drop_column('scores')


def downgrade() -> None:
    op.execute(
        test_runs_status.update()
        .where(test_runs_status.c.status == "green")
        .values(status="completed")
    )
    op.execute(
        test_runs_status.update()
        .where(test_runs_status.c.status.in_(("amber", "red", "not_ran")))
        .values(status="failed")
    )

    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('scores', sa.JSON(), nullable=True))
        batch_op.drop_column('results')
