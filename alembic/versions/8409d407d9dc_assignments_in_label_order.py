"""store assignment copies and results in label order

Revision ID: 8409d407d9dc
Revises: e47a76f674f0
Create Date: 2026-10-01 22:00:00.000000

A test's assignments are listed in one order everywhere: by label, ignoring
letter case. A test's own assignments are rows, sorted when the API returns
them; frozen copies (test_set_entries, standalone_runs) are JSON lists,
saved sorted from now on, and a run's results are written in the copy's
order. This sorts what's already stored the same way, so older copies and
results read like new ones. Order only: no value changes, so there's
nothing to undo.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '8409d407d9dc'
down_revision: str | None = 'e47a76f674f0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sorted_copy(assignments):
    if not isinstance(assignments, list):
        return assignments
    return sorted(assignments, key=lambda a: str(a.get("label") or a.get("name") or "").casefold())


def _sorted_results(results):
    if not isinstance(results, dict):
        return results
    return dict(sorted(results.items(), key=lambda item: item[0].casefold()))


def _rewrite(table_name: str, column: str, rewrite) -> None:
    connection = op.get_bind()
    table = sa.table(table_name, sa.column("id", sa.Uuid()), sa.column(column, sa.JSON()))
    for row_id, value in connection.execute(sa.select(table.c.id, table.c[column])).all():
        rewritten = rewrite(value)
        # compared as lists of items: dict equality ignores order
        if value and list(_items(rewritten)) != list(_items(value)):
            connection.execute(table.update().where(table.c.id == row_id)
                               .values(**{column: rewritten}))


def _items(value):
    return value.items() if isinstance(value, dict) else value


def upgrade() -> None:
    _rewrite("test_set_entries", "test_type_assignments", _sorted_copy)
    _rewrite("standalone_runs", "test_type_assignments", _sorted_copy)
    _rewrite("test_runs", "results", _sorted_results)


def downgrade() -> None:
    # Order only — the previous order carried no meaning to restore.
    pass
