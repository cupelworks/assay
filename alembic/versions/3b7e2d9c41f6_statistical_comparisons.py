# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""statistical comparisons: two finished batches compared check by check

Revision ID: 3b7e2d9c41f6
Revises: 90b64c0a5c27
Create Date: 2026-10-02 12:00:00.000000

Did my change help? (docs/version_1/statistics/ notes 8, 18, 22): a
`statistical_comparisons` row per comparison — the two batches (A the
baseline, B the change), their shared scope, the statistical test and its
parameters, an optional note, and the result, computed when the comparison is
created.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '3b7e2d9c41f6'
down_revision: str | None = '90b64c0a5c27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXED = ("batch_a_id", "batch_b_id", "test_id", "test_set_id", "test_plan_id")


def upgrade() -> None:
    op.create_table(
        "statistical_comparisons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_a_id", sa.Uuid(), nullable=False),
        sa.Column("batch_b_id", sa.Uuid(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=True),
        sa.Column("test_set_id", sa.Uuid(), nullable=True),
        sa.Column("test_plan_id", sa.Uuid(), nullable=True),
        sa.Column("statistical_test", sa.Text(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["batch_a_id"], ["statistical_batches.id"],
                                name="fk_statistical_comparisons_batch_a_id"),
        sa.ForeignKeyConstraint(["batch_b_id"], ["statistical_batches.id"],
                                name="fk_statistical_comparisons_batch_b_id"),
        sa.ForeignKeyConstraint(["test_id"], ["tests.id"],
                                name="fk_statistical_comparisons_test_id"),
        sa.ForeignKeyConstraint(["test_set_id"], ["test_sets.id"],
                                name="fk_statistical_comparisons_test_set_id"),
        sa.ForeignKeyConstraint(["test_plan_id"], ["test_plans.id"],
                                name="fk_statistical_comparisons_test_plan_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in _INDEXED:
        op.create_index(f"ix_statistical_comparisons_{column}", "statistical_comparisons",
                        [column])


def downgrade() -> None:
    for column in _INDEXED:
        op.drop_index(f"ix_statistical_comparisons_{column}", "statistical_comparisons")
    op.drop_table("statistical_comparisons")
