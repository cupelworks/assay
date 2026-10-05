# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""statistical batches, batch_id and batch_index on runs and executions, errored on results

Revision ID: 90b64c0a5c27
Revises: 8409d407d9dc
Create Date: 2026-10-02 09:00:00.000000

Run with statistics: a `statistical_batches` row per
batch — the scope (exactly one of a test, a test set, a test plan), the
statistical test and its parameters, the times requested, the plan (floor and
calls per time), the status (its own enum) and, once every run has
finished, the result. The runs and executions a batch creates carry its id and
the time (1-based) they belong to.

Every stored check result also gains `errored` (false): the worker now marks
a check that raised an error, so statistics can count it apart from a real
failure. Results written before can't be told apart; they read as evaluated,
as they always did.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '90b64c0a5c27'
down_revision: str | None = '8409d407d9dc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ("pending", "running", "passed", "failed", "inconclusive", "incomplete",
             "not_ran")
_TABLES = ("test_runs", "test_set_executions", "test_plan_executions")


def _rewrite_results(change) -> None:
    connection = op.get_bind()
    runs = sa.table("test_runs", sa.column("id", sa.Uuid()), sa.column("results", sa.JSON()))
    for run_id, results in connection.execute(
            sa.select(runs.c.id, runs.c.results).where(runs.c.results.is_not(None))).all():
        if not isinstance(results, dict):
            continue
        changed = {label: change(result) if isinstance(result, dict) else result
                   for label, result in results.items()}
        if changed != results:
            connection.execute(runs.update().where(runs.c.id == run_id)
                               .values(results=changed))


def upgrade() -> None:
    op.create_table(
        "statistical_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=True),
        sa.Column("test_set_id", sa.Uuid(), nullable=True),
        sa.Column("test_plan_id", sa.Uuid(), nullable=True),
        sa.Column("statistical_test", sa.Text(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("times_requested", sa.Integer(), nullable=False),
        sa.Column("runs_per_time", sa.Integer(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum(*_STATUSES, name="batchstatus", create_constraint=True),
                  nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["test_id"], ["tests.id"],
                                name="fk_statistical_batches_test_id"),
        sa.ForeignKeyConstraint(["test_set_id"], ["test_sets.id"],
                                name="fk_statistical_batches_test_set_id"),
        sa.ForeignKeyConstraint(["test_plan_id"], ["test_plans.id"],
                                name="fk_statistical_batches_test_plan_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("test_id", "test_set_id", "test_plan_id", "status"):
        op.create_index(f"ix_statistical_batches_{column}", "statistical_batches", [column])
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("batch_id", sa.Uuid(), nullable=True))
            batch_op.add_column(sa.Column("batch_index", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(f"fk_{table}_batch_id", "statistical_batches",
                                        ["batch_id"], ["id"])
            batch_op.create_index(f"ix_{table}_batch_id", ["batch_id"])
    _rewrite_results(lambda result: {**result, "errored": bool(result.get("errored"))})


def downgrade() -> None:
    _rewrite_results(lambda result: {k: v for k, v in result.items() if k != "errored"})
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_batch_id")
            batch_op.drop_constraint(f"fk_{table}_batch_id", type_="foreignkey")
            batch_op.drop_column("batch_index")
            batch_op.drop_column("batch_id")
    for column in ("test_id", "test_set_id", "test_plan_id", "status"):
        op.drop_index(f"ix_statistical_batches_{column}", "statistical_batches")
    op.drop_table("statistical_batches")
