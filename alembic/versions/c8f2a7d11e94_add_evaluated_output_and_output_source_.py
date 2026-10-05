# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""add evaluated_output and output_source to test_runs

Revision ID: c8f2a7d11e94
Revises: b4e1c9d27a58
Create Date: 2026-09-27 15:00:00.000000

A run records the answer it scored and where that answer came from - the copy's
recorded model_output, or the application under test called during the
run. Both nullable: null while Pending/Running and for NotRan, and null on
every run executed before this landed (no backfill - those runs scored the
copy's model_output, which the run detail still shows, and nothing is in
production).

output_source follows how cost/comparison were added: the column bare,
then the CHECK constraint explicitly, since SQLite batch mode can't add a
constraint-bearing enum column in one step.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c8f2a7d11e94'
down_revision: Union[str, None] = 'b4e1c9d27a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('evaluated_output', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column(
            'output_source', sa.Enum('recorded', 'application', name='outputsource'),
            nullable=True,
        ))
        batch_op.create_check_constraint(
            'outputsource', "output_source IN ('recorded', 'application')"
        )


def downgrade() -> None:
    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.drop_constraint('outputsource', type_='check')
        batch_op.drop_column('output_source')
        batch_op.drop_column('evaluated_output')
