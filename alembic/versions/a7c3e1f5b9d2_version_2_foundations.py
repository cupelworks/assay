"""version 2 foundations: indexes, a dataset row's position, a run's check types

Revision ID: a7c3e1f5b9d2
Revises: f8a0b2c4d6e7
Create Date: 2026-10-04 10:00:00.000000

What the version 2 lists read and filter by:

- Indexes for the new lookups: a test's copies in sets
  (`test_set_entries.test_id`), the tests made from a dataset row
  (`tests.dataset_row_id`, also the set-null check when a row is deleted),
  tests by creation time (`tests.created_at`) and tests by check type
  (`test_type_assignments.test_type_name`).
- `dataset_rows.position`: a row's number within its dataset, from 1, unique
  per dataset. Existing rows are numbered in the order they were inserted.
- `test_run_check_types`: the check types each run asks, one row per type, so
  runs can be filtered by check type the same way on SQLite and Postgres.
  Filled for existing runs from their frozen copy's checks, leaving out a
  batch's left-out labels.

"""
import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a7c3e1f5b9d2'
down_revision: str | None = 'f8a0b2c4d6e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = [
    ("ix_test_set_entries_test_id", "test_set_entries", ["test_id"]),
    ("ix_tests_dataset_row_id", "tests", ["dataset_row_id"]),
    ("ix_tests_created_at", "tests", ["created_at"]),
    ("ix_test_type_assignments_test_type_name", "test_type_assignments", ["test_type_name"]),
]
_INSERTION_ORDER = {"sqlite": "rowid", "postgresql": "ctid"}
_CHUNK = 1000


def _number_rows() -> None:
    """Every row's position: its place among its dataset's rows in the order
    they were inserted, from 1."""
    bind = op.get_bind()
    order = _INSERTION_ORDER.get(bind.dialect.name, "id")
    bind.execute(sa.text(f"""
        UPDATE dataset_rows SET position = numbered.n
        FROM (SELECT id, row_number() OVER (PARTITION BY dataset_id ORDER BY {order}) AS n
              FROM dataset_rows) AS numbered
        WHERE dataset_rows.id = numbered.id
    """))


def _check_types(assignments: str | list | None, skip_labels: str | list | None) -> set[str]:
    """The check types a run asks: its copy's checks, without the labels a
    batch left out."""
    def loaded(value):
        return json.loads(value) if isinstance(value, str) else (value or [])
    skipped = set(loaded(skip_labels))
    return {a["name"] for a in loaded(assignments) if a.get("label") not in skipped}


def _fill_check_types() -> None:
    bind = op.get_bind()
    runs = bind.execute(sa.text("""
        SELECT r.id, COALESCE(s.test_type_assignments, e.test_type_assignments), r.skip_labels
        FROM test_runs r
        LEFT JOIN standalone_runs s ON s.id = r.id
        LEFT JOIN test_set_entries e ON e.id = r.test_set_entry_id
    """)).all()
    rows = [{"run_id": run_id, "test_type_name": name}
            for run_id, assignments, skip_labels in runs
            for name in sorted(_check_types(assignments, skip_labels))]
    table = sa.table("test_run_check_types", sa.column("run_id"), sa.column("test_type_name"))
    for start in range(0, len(rows), _CHUNK):
        bind.execute(table.insert(), rows[start:start + _CHUNK])


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns)

    with op.batch_alter_table("dataset_rows") as batch:
        batch.add_column(sa.Column("position", sa.Integer(), nullable=True))
    _number_rows()
    with op.batch_alter_table("dataset_rows") as batch:
        batch.alter_column("position", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint("uq_dataset_rows_dataset_id_position",
                                       ["dataset_id", "position"])

    op.create_table(
        "test_run_check_types",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("test_type_name", sa.Text(), primary_key=True),
    )
    op.create_index("ix_test_run_check_types_test_type_name", "test_run_check_types",
                    ["test_type_name"])
    _fill_check_types()


def downgrade() -> None:
    op.drop_index("ix_test_run_check_types_test_type_name", table_name="test_run_check_types")
    op.drop_table("test_run_check_types")
    with op.batch_alter_table("dataset_rows") as batch:
        batch.drop_constraint("uq_dataset_rows_dataset_id_position", type_="unique")
        batch.drop_column("position")
    for name, table, _ in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
