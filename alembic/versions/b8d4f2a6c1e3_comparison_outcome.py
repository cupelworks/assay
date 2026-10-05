# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""a comparison's outcome, stored so the comparisons list can filter and count by it

Revision ID: b8d4f2a6c1e3
Revises: a7c3e1f5b9d2
Create Date: 2026-10-04 13:00:00.000000

`statistical_comparisons.outcome`: the comparison's one overall answer
(`worse`, `better`, `no_worse`, `inconclusive`, `no_difference`, `none`),
read off its checks' verdicts. A comparison never changes once stored, so its
outcome is written when it's created; existing comparisons get theirs from
their stored verdicts, by the same rule the API applies.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b8d4f2a6c1e3'
down_revision: str | None = 'a7c3e1f5b9d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_comparisons = sa.table("statistical_comparisons", sa.column("id", sa.Uuid),
                        sa.column("result", sa.JSON), sa.column("outcome", sa.Text))


def upgrade() -> None:
    from assay.services.statistics.compare import outcome

    op.add_column("statistical_comparisons", sa.Column("outcome", sa.Text(), nullable=True))
    connection = op.get_bind()
    for id_, result in connection.execute(sa.select(_comparisons.c.id, _comparisons.c.result)):
        connection.execute(_comparisons.update().where(_comparisons.c.id == id_)
                           .values(outcome=outcome(result["verdicts"]).value))
    with op.batch_alter_table("statistical_comparisons") as batch:
        batch.alter_column("outcome", existing_type=sa.Text(), nullable=False)
        batch.create_index("ix_statistical_comparisons_outcome", ["outcome"])


def downgrade() -> None:
    with op.batch_alter_table("statistical_comparisons") as batch:
        batch.drop_index("ix_statistical_comparisons_outcome")
        batch.drop_column("outcome")
