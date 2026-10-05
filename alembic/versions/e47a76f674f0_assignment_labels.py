# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""assignment labels: a test type can be assigned more than once

Revision ID: e47a76f674f0
Revises: d649f666f728
Create Date: 2026-10-01 20:00:00.000000

Each assignment gets a `label`, unique within its test, and a run's results
are keyed by it — so a test can have two Contains checks, two JSON Field
Equals on different paths, and so on.

- test_type_assignments gains `label`, set to the type's name, and its
  primary key becomes (test_id, label) instead of (test_id, test_type_name).
- Every frozen copy — test_set_entries and standalone_runs — gets a label on
  each assignment: the type's name, numbered when the copy already has that
  type ("Contains 2"). A copy could hold a type twice before this: the entry
  PATCH didn't refuse it.
- Every stored result gains `test_type`, the type it ran: its key, since
  results were keyed by type name until now.

The downgrade drops the labels and `test_type` and restores the old key; it
fails, as it should, while a test has a type assigned twice.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'e47a76f674f0'
down_revision: str | None = 'd649f666f728'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COPIES = ("test_set_entries", "standalone_runs")


def _labelled(assignments: list[dict]) -> list[dict]:
    """The labelling rule as it stands at this migration: a label kept as it
    is, else the type's name, numbered past every label in use (ignoring
    letter case)."""
    taken = {a["label"].casefold() for a in assignments if a.get("label")}
    result = []
    for assignment in assignments:
        assignment = dict(assignment)
        if not assignment.get("label"):
            label, number = assignment["name"], 1
            while label.casefold() in taken:
                number += 1
                label = f"{assignment['name']} {number}"
            taken.add(label.casefold())
            assignment["label"] = label
        result.append(assignment)
    return result


def _rewrite_json(table_name: str, column: str, rewrite) -> None:
    connection = op.get_bind()
    table = sa.table(table_name, sa.column("id", sa.Uuid()), sa.column(column, sa.JSON()))
    for row_id, value in connection.execute(sa.select(table.c.id, table.c[column])).all():
        if not value:
            continue
        rewritten = rewrite(value)
        if rewritten != value:
            connection.execute(table.update().where(table.c.id == row_id)
                               .values(**{column: rewritten}))


def _with_test_type(results):
    if not isinstance(results, dict):
        return results
    return {key: ({**result, "test_type": result.get("test_type") or key}
                  if isinstance(result, dict) else result)
            for key, result in results.items()}


def _without_test_type(results):
    if not isinstance(results, dict):
        return results
    return {key: ({k: v for k, v in result.items() if k != "test_type"}
                  if isinstance(result, dict) else result)
            for key, result in results.items()}


def upgrade() -> None:
    with op.batch_alter_table("test_type_assignments") as batch_op:
        batch_op.add_column(sa.Column("label", sa.Text(), nullable=True))
    op.execute("UPDATE test_type_assignments SET label = test_type_name")
    # recreate: SQLite can't change a primary key in place, and on PostgreSQL
    # the copy keeps this one migration the same on both
    with op.batch_alter_table("test_type_assignments", recreate="always") as batch_op:
        batch_op.alter_column("label", existing_type=sa.Text(), nullable=False)
        batch_op.create_primary_key("pk_test_type_assignments", ["test_id", "label"])

    for table_name in _COPIES:
        _rewrite_json(table_name, "test_type_assignments", _labelled)
    _rewrite_json("test_runs", "results", _with_test_type)


def downgrade() -> None:
    _rewrite_json("test_runs", "results", _without_test_type)
    for table_name in _COPIES:
        _rewrite_json(table_name, "test_type_assignments",
                      lambda items: [{k: v for k, v in a.items() if k != "label"} for a in items])
    with op.batch_alter_table("test_type_assignments", recreate="always") as batch_op:
        batch_op.create_primary_key("pk_test_type_assignments", ["test_id", "test_type_name"])
        batch_op.drop_column("label")
