# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""add test_type_assignments.answer_path and test_runs.application_reply

Revision ID: 5ff0acda2b2c
Revises: 1aac7a522b8c
Create Date: 2026-09-30 22:00:00.000000

A run keeps the application's whole reply (`application_reply`), and each
assigned check can read its own part of it (`answer_path`, a JSONPath)
instead of the answer at the settings' output path. Both nullable: null
means "the default answer" on an assignment, and "no reply" (a recorded
answer, NotRan, or a run from before this existed) on a run. Test set
entries and standalone runs keep their assignments as JSON, so their
copies just gain an `answer_path` key — no column to add there.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '5ff0acda2b2c'
down_revision: str | None = '1aac7a522b8c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('test_type_assignments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('answer_path', sa.Text(), nullable=True))
    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('application_reply', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.drop_column('application_reply')
    with op.batch_alter_table('test_type_assignments', schema=None) as batch_op:
        batch_op.drop_column('answer_path')
