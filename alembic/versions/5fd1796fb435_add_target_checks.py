"""add target_checks: checks of the application-under-test settings

Revision ID: 5fd1796fb435
Revises: a0f4ff4fd23a
Create Date: 2026-09-27 17:30:00.000000

A check is one call to the application under test made by a worker, with the complete settings checked stored on
the row and the outcome written back for the UI to poll.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5fd1796fb435'
down_revision: Union[str, None] = 'a0f4ff4fd23a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('target_checks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'running', 'completed', name='targetcheckstatus',
                                create_constraint=True), nullable=False),
    sa.Column('input', sa.Text(), nullable=False),
    sa.Column('settings', sa.JSON(), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=True),
    sa.Column('status_code', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('target_checks')
