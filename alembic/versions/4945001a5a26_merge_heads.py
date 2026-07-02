"""merge heads

Revision ID: 4945001a5a26
Revises: 39de048bc57a, 8f93dfb8cdc4
Create Date: 2026-05-27 18:54:46.214011

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4945001a5a26'
down_revision: Union[str, None] = ('39de048bc57a', '8f93dfb8cdc4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
