"""updating test models

Revision ID: 20cf133882df
Revises: a32c5cc3f97a
Create Date: 2026-05-28 01:36:02.293968

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

revision: str = '20cf133882df'
down_revision: Union[str, None] = 'a32c5cc3f97a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('test_plans',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('test_sets',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('test_plan_entries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('test_plan_id', sa.Uuid(), nullable=False),
    sa.Column('test_set_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['test_plan_id'], ['test_plans.id'], ),
    sa.ForeignKeyConstraint(['test_set_id'], ['test_sets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('test_plan_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_test_plan_entries_test_plan_id'), ['test_plan_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_test_plan_entries_test_set_id'), ['test_set_id'], unique=False)

    op.create_table('tests',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('dataset_row_id', sa.Uuid(), nullable=True),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('input', sa.Text(), nullable=False),
    sa.Column('expected_output', sa.Text(), nullable=False),
    sa.Column('model_output', sa.Text(), nullable=False),
    sa.Column('test_type_ids', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['dataset_row_id'], ['dataset_rows.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('test_set_entries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('test_set_id', sa.Uuid(), nullable=False),
    sa.Column('test_id', sa.Uuid(), nullable=False),
    sa.Column('input', sa.Text(), nullable=False),
    sa.Column('expected_output', sa.Text(), nullable=True),
    sa.Column('test_type_ids', sa.JSON(), nullable=False),
    sa.Column('snapshot_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['test_id'], ['tests.id'], ),
    sa.ForeignKeyConstraint(['test_set_id'], ['test_sets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('test_set_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_test_set_entries_test_set_id'), ['test_set_id'], unique=False)

    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('test_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('test_set_entry_id', sa.Uuid(), nullable=True))
        batch_op.drop_index(batch_op.f('ix_test_runs_dataset_row_id'))
        batch_op.create_index(batch_op.f('ix_test_runs_test_id'), ['test_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_test_runs_test_set_entry_id'), ['test_set_entry_id'], unique=False)
        batch_op.create_foreign_key('fk_test_runs_test_id', 'tests', ['test_id'], ['id'])
        batch_op.create_foreign_key('fk_test_runs_test_set_entry_id', 'test_set_entries', ['test_set_entry_id'], ['id'])
        batch_op.drop_column('latency_ms')
        batch_op.drop_column('test_type_ids')
        batch_op.drop_column('dataset_row_id')


def downgrade() -> None:
    with op.batch_alter_table('test_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dataset_row_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('test_type_ids', sa.JSON(), nullable=False))
        batch_op.add_column(sa.Column('latency_ms', sa.Float(), nullable=True))
        batch_op.drop_constraint('fk_test_runs_test_set_entry_id', type_='foreignkey')
        batch_op.drop_constraint('fk_test_runs_test_id', type_='foreignkey')
        batch_op.create_foreign_key('fk_test_runs_dataset_row_id', 'dataset_rows', ['dataset_row_id'], ['id'])
        batch_op.drop_index(batch_op.f('ix_test_runs_test_set_entry_id'))
        batch_op.drop_index(batch_op.f('ix_test_runs_test_id'))
        batch_op.create_index(batch_op.f('ix_test_runs_dataset_row_id'), ['dataset_row_id'], unique=False)
        batch_op.drop_column('test_set_entry_id')
        batch_op.drop_column('test_id')

    with op.batch_alter_table('test_set_entries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_test_set_entries_test_set_id'))

    op.drop_table('test_set_entries')
    op.drop_table('tests')

    with op.batch_alter_table('test_plan_entries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_test_plan_entries_test_set_id'))
        batch_op.drop_index(batch_op.f('ix_test_plan_entries_test_plan_id'))

    op.drop_table('test_plan_entries')
    op.drop_table('test_sets')
    op.drop_table('test_plans')