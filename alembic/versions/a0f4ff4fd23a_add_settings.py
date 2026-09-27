"""add settings: settings saved from the UI, one row per group

Revision ID: a0f4ff4fd23a
Revises: c8f2a7d11e94
Create Date: 2026-09-27 17:00:00.000000

One row per group of settings, the group's complete settings as one JSON
object, its shape defined and validated in code (schemas/settings.py).
Created empty on purpose: a group without a row takes its settings from the
environment, which is exactly the behaviour before this table existed.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a0f4ff4fd23a'
down_revision: Union[str, None] = 'c8f2a7d11e94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('settings',
    sa.Column('section', sa.Enum('target', name='settingssection', create_constraint=True),
              nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('section')
    )


def downgrade() -> None:
    op.drop_table('settings')
