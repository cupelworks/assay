# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""drop statistical_verifications: the z-test calculator is gone

Revision ID: 5d8a1f3c9e27
Revises: 3b7e2d9c41f6
Create Date: 2026-10-02 15:00:00.000000

The one-sample z-test endpoint never wrote this table (0 rows everywhere); the
one-sample t-test of a statistical batch does its job properly. The downgrade
recreates the table as the migrations that built it left it (39de048bc57a,
dd380d11e3fa, a32c5cc3f97a), empty.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '5d8a1f3c9e27'
down_revision: str | None = '3b7e2d9c41f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_statistical_verifications_executed_test_id",
                  table_name="statistical_verifications")
    op.drop_table("statistical_verifications")


def downgrade() -> None:
    op.create_table(
        "statistical_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("executed_test_id", sa.Uuid(), nullable=True),
        sa.Column("metric", sa.String(length=255), nullable=False),
        sa.Column("test_type", sa.String(length=50), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("alpha", sa.Float(), nullable=False),
        sa.Column("alternative", sa.String(length=20), nullable=False),
        sa.Column("z_statistic", sa.Float(), nullable=False),
        sa.Column("p_value", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("result_detail", sa.JSON(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["executed_test_id"], ["test_runs.id"],
                                name="fk_statistical_verifications_executed_test_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_statistical_verifications_executed_test_id",
                    "statistical_verifications", ["executed_test_id"])
