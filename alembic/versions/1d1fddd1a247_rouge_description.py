# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""describe what the ROUGE row measures: ROUGE-L F1

Revision ID: 1d1fddd1a247
Revises: 5ff0acda2b2c
Create Date: 2026-09-30 23:30:00.000000

The ROUGE row scores ROUGE-L F1 — the longest in-order sequence of words the
answer shares with the expected text — but its description still read
"n-gram overlap", which is what ROUGE-1 and ROUGE-2 measure. The downgrade
puts the old text back.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '1d1fddd1a247'
down_revision: str | None = '5ff0acda2b2c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "Measures n-gram overlap between output and expected text."
_NEW = ("Measures the longest sequence of words the output shares with the expected text, "
        "in order (ROUGE-L F1).")

_test_types = sa.table(
    "test_types",
    sa.column("name", sa.Text()),
    sa.column("description", sa.Text()),
)


def _describe_rouge(description: str) -> None:
    op.get_bind().execute(_test_types.update().where(_test_types.c.name == "ROUGE")
                          .values(description=description))


def upgrade() -> None:
    _describe_rouge(_NEW)


def downgrade() -> None:
    _describe_rouge(_OLD)
