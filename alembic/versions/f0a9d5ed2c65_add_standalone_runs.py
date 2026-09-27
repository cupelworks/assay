"""add standalone_runs: a frozen copy of the test for every standalone run

Revision ID: f0a9d5ed2c65
Revises: c3e6b3074e69
Create Date: 2026-09-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f0a9d5ed2c65'
down_revision: Union[str, None] = 'c3e6b3074e69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No backfill for standalone runs that already exist: nothing is in
    # production, and
    # local databases are recreated rather than migrated. Such runs have no
    # row here and their detail endpoint can't be served.
    op.create_table('standalone_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('input', sa.Text(), nullable=False),
    sa.Column('expected_output', sa.Text(), nullable=True),
    sa.Column('model_output', sa.Text(), nullable=True),
    sa.Column('test_type_assignments', sa.JSON(), nullable=False),
    sa.Column('snapshot_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['id'], ['test_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('standalone_runs')
