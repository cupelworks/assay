# ruff: noqa: E501
"""add test plan and test set execution tracking

Revision ID: b039ee8d1fef
Revises: 03a774b2b416
Create Date: 2026-07-12 22:57:21.557836

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b039ee8d1fef'
down_revision: str | None = '03a774b2b416'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('test_plan_executions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('test_plan_id', sa.Uuid(), nullable=False),
    sa.Column('replayed_execution_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['replayed_execution_id'], ['test_plan_executions.id'], ),
    sa.ForeignKeyConstraint(['test_plan_id'], ['test_plans.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('test_plan_executions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_test_plan_executions_replayed_execution_id'), ['replayed_execution_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_test_plan_executions_test_plan_id'), ['test_plan_id'], unique=False)

    op.create_table('test_set_executions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('test_set_id', sa.Uuid(), nullable=False),
    sa.Column('replayed_execution_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['replayed_execution_id'], ['test_set_executions.id'], ),
    sa.ForeignKeyConstraint(['test_set_id'], ['test_sets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('test_set_executions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_test_set_executions_replayed_execution_id'), ['replayed_execution_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_test_set_executions_test_set_id'), ['test_set_id'], unique=False)

    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('test_set_execution_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('test_plan_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('test_plan_execution_id', sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f('ix_test_runs_test_plan_execution_id'), ['test_plan_execution_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_test_runs_test_plan_id'), ['test_plan_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_test_runs_test_set_execution_id'), ['test_set_execution_id'], unique=False)
        batch_op.create_foreign_key('fk_test_runs_test_set_execution_id', 'test_set_executions', ['test_set_execution_id'], ['id'])
        batch_op.create_foreign_key('fk_test_runs_test_plan_execution_id', 'test_plan_executions', ['test_plan_execution_id'], ['id'])
        batch_op.create_foreign_key('fk_test_runs_test_plan_id', 'test_plans', ['test_plan_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_test_runs_test_plan_id', type_='foreignkey')
        batch_op.drop_constraint('fk_test_runs_test_plan_execution_id', type_='foreignkey')
        batch_op.drop_constraint('fk_test_runs_test_set_execution_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_test_runs_test_set_execution_id'))
        batch_op.drop_index(batch_op.f('ix_test_runs_test_plan_id'))
        batch_op.drop_index(batch_op.f('ix_test_runs_test_plan_execution_id'))
        batch_op.drop_column('test_plan_execution_id')
        batch_op.drop_column('test_plan_id')
        batch_op.drop_column('test_set_execution_id')

    with op.batch_alter_table('test_set_executions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_test_set_executions_test_set_id'))
        batch_op.drop_index(batch_op.f('ix_test_set_executions_replayed_execution_id'))

    op.drop_table('test_set_executions')
    with op.batch_alter_table('test_plan_executions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_test_plan_executions_test_plan_id'))
        batch_op.drop_index(batch_op.f('ix_test_plan_executions_replayed_execution_id'))

    op.drop_table('test_plan_executions')
