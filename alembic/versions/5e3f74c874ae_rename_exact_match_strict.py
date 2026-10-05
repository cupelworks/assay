# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""rename Exact Match (strict) to Exact Match (whitespace-sensitive)

Revision ID: 5e3f74c874ae
Revises: dbc67550f049
Create Date: 2026-09-29 22:00:00.000000

"Strict" didn't say what it was strict about: the row differs from Exact
Match only in not trimming spaces, tabs and newlines at the start or end.
The new name and texts say so. Engine and settings are unchanged.

Assignments point at a type by name, and test_type_assignments'
foreign key to test_types.name has no ON UPDATE CASCADE, so renaming the
row in place would break the constraint wherever it's enforced. Instead:
insert the renamed row, move the live assignments to it, delete the old
row — every statement leaves the key valid. Test set entries' copies of
their assignments are renamed too, so replaying an execution still finds
the type. Past runs' results and standalone runs' copies keep the name the
type had when they ran: nothing looks them up again.

"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '5e3f74c874ae'
down_revision: str | None = 'dbc67550f049'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = {
    "name": "Exact Match (strict)",
    "description": "Checks if the output equals the expected string character for "
                   "character, including case and surrounding whitespace.",
    "best_for": "Outputs read by a machine where every character counts: IDs, codes, "
                "exact formats.",
    "limitations": "Fails on a trailing newline or space that the plain Exact Match "
                   "forgives.",
}
_NEW = {
    "name": "Exact Match (whitespace-sensitive)",
    "description": "Checks if the output equals the expected string exactly, including "
                   "letter case and any spaces, tabs or newlines at the start or end — "
                   "unlike Exact Match, which ignores those.",
    "best_for": "Outputs used byte for byte, where a stray leading or trailing space or "
                "newline would break something: codes, IDs, fixed-width or "
                "machine-parsed values.",
    "limitations": "Fails on a trailing newline or space that a reader wouldn't notice "
                   "and that Exact Match forgives.",
}

_test_types = sa.table(
    "test_types",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.Text()),
    sa.column("category", sa.Text()),
    sa.column("description", sa.Text()),
    sa.column("best_for", sa.Text()),
    sa.column("cost", sa.Text()),
    sa.column("limitations", sa.Text()),
    sa.column("config_fields", sa.JSON()),
    sa.column("engine", sa.Text()),
    sa.column("engine_settings", sa.JSON()),
    sa.column("comparison", sa.Text()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime()),
)
_assignments = sa.table(
    "test_type_assignments",
    sa.column("test_type_name", sa.Text()),
)
_entries = sa.table(
    "test_set_entries",
    sa.column("id", sa.Uuid()),
    sa.column("test_type_assignments", sa.JSON()),
)


def _rename(old: dict, new: dict) -> None:
    connection = op.get_bind()
    row = connection.execute(
        sa.select(_test_types).where(_test_types.c.name == old["name"])
    ).mappings().one()

    connection.execute(_test_types.insert().values({**row, **new, "id": uuid.uuid4()}))
    connection.execute(
        _assignments.update()
        .where(_assignments.c.test_type_name == old["name"])
        .values(test_type_name=new["name"])
    )
    connection.execute(_test_types.delete().where(_test_types.c.id == row["id"]))

    for entry_id, assignments in connection.execute(
        sa.select(_entries.c.id, _entries.c.test_type_assignments)
    ):
        if any(item.get("name") == old["name"] for item in assignments or []):
            renamed = [
                {**item, "name": new["name"]} if item.get("name") == old["name"] else item
                for item in assignments
            ]
            connection.execute(
                _entries.update().where(_entries.c.id == entry_id)
                .values(test_type_assignments=renamed)
            )


def upgrade() -> None:
    _rename(_OLD, _NEW)


def downgrade() -> None:
    _rename(_NEW, _OLD)
