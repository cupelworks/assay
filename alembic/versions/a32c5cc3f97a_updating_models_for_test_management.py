# ruff: noqa: E501
"""updating models for test management

Revision ID: a32c5cc3f97a
Revises: 89844d4255ff
Create Date: 2026-05-27 21:43:24.072049

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

from alembic import op

revision: str = 'a32c5cc3f97a'
down_revision: str | None = '89844d4255ff'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('test_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('dataset_row_id', sa.Uuid(), nullable=True),
    sa.Column('test_type_ids', sa.JSON(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'running', 'completed', 'failed', name='teststatus'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('scores', sa.JSON(), nullable=True),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('executed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['dataset_row_id'], ['dataset_rows.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_test_runs_dataset_row_id'), ['dataset_row_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_test_runs_status'), ['status'], unique=False)

    # Fix statistical_verifications BEFORE dropping executed_tests.
    # Using add+drop instead of rename so the unnamed FK to executed_tests
    # is dropped together with the old column, not preserved in the new schema.
    with op.batch_alter_table('statistical_verifications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('test_run_id', sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f('ix_statistical_verifications_test_run_id'), ['test_run_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_statistical_verifications_test_run_id',
            'test_runs', ['test_run_id'], ['id']
        )
        batch_op.drop_index(batch_op.f('ix_statistical_verifications_executed_test_id'))
        batch_op.drop_column('executed_test_id')

    op.drop_table('pending_tests')
    with op.batch_alter_table('executed_tests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_executed_tests_pending_test_id'))
    op.drop_table('executed_tests')

    with op.batch_alter_table('test_types', schema=None) as batch_op:
        batch_op.alter_column('cost',
               existing_type=sa.VARCHAR(length=21),
               type_=sa.Enum('very_fast', 'fast', 'expensive', name='testtypescost'),
               existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('test_types', schema=None) as batch_op:
        batch_op.alter_column('cost',
               existing_type=sa.Enum('very_fast', 'fast', 'expensive', name='testtypescost'),
               type_=sa.VARCHAR(length=21),
               existing_nullable=True)

    with op.batch_alter_table('statistical_verifications', schema=None) as batch_op:
        batch_op.drop_constraint('fk_statistical_verifications_test_run_id', type_='foreignkey')
        batch_op.add_column(sa.Column('executed_test_id', sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f('ix_statistical_verifications_executed_test_id'), ['executed_test_id'], unique=False)
        batch_op.drop_column('test_run_id')

    op.create_table('executed_tests',
    sa.Column('id', sa.CHAR(length=32), nullable=False),
    sa.Column('pending_test_id', sa.CHAR(length=32), nullable=False),
    sa.Column('actual_output', sa.TEXT(), nullable=False),
    sa.Column('scores', sqlite.JSON(), nullable=False),
    sa.Column('latency_ms', sa.FLOAT(), nullable=True),
    sa.Column('error', sa.TEXT(), nullable=True),
    sa.Column('executed_at', sa.DATETIME(), nullable=False),
    sa.ForeignKeyConstraint(['pending_test_id'], ['pending_tests.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('executed_tests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_executed_tests_pending_test_id'), ['pending_test_id'], unique=False)

    op.create_table('pending_tests',
    sa.Column('id', sa.CHAR(length=32), nullable=False),
    sa.Column('input', sa.TEXT(), nullable=False),
    sa.Column('expected_output', sa.TEXT(), nullable=False),
    sa.Column('model_output', sa.TEXT(), nullable=False),
    sa.Column('metrics', sqlite.JSON(), nullable=False),
    sa.Column('status', sa.VARCHAR(length=9), nullable=False),
    sa.Column('created_at', sa.DATETIME(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_test_runs_status'))
        batch_op.drop_index(batch_op.f('ix_test_runs_dataset_row_id'))

    op.drop_table('test_runs')