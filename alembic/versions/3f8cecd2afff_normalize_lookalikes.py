"""normalize look-alike characters in the text checks

Revision ID: 3f8cecd2afff
Revises: b795f3711490
Create Date: 2026-10-01 12:00:00.000000

The exact_match, contains and regex engines take a new setting,
`normalize_lookalikes`: before comparing, a curly quote becomes a straight
one, a non-breaking or other special space an ordinary space, a look-alike
hyphen a hyphen-minus, an invisible character is removed and an accented
letter gets one encoding. It is written on every row of those engines, on,
so GET /tests/types shows it and every result records it with the rest of
the row's settings; a result recorded before this migration has no such key
because it was scored without it.

Exact Match (whitespace-sensitive) is the exception: it exists for outputs
where every character counts, so the setting is off there, and its texts
now say it also tells look-alike characters apart.

Rows are matched by engine, not by name, so a row added by hand to one of
these engines gets the setting too. The downgrade removes the key from every
row and restores the whitespace-sensitive row's texts.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '3f8cecd2afff'
down_revision: str | None = 'b795f3711490'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENGINES = ("exact_match", "contains", "regex")
_SETTING = "normalize_lookalikes"
_EXACT = "Exact Match (whitespace-sensitive)"

_EXACT_BEFORE = {
    "description": "Checks if the output equals the expected string exactly, including "
                   "letter case and any spaces, tabs or newlines at the start or end — "
                   "unlike Exact Match, which ignores those.",
    "limitations": "Fails on a trailing newline or space that a reader wouldn't notice and "
                   "that Exact Match forgives.",
}
_EXACT_AFTER = {
    "description": "Checks if the output equals the expected string exactly, including "
                   "letter case, any spaces, tabs or newlines at the start or end, and "
                   "look-alike characters such as a curly quote or a non-breaking space — "
                   "unlike Exact Match, which ignores those.",
    "limitations": "Fails on a trailing newline, a curly quote or a special space that a "
                   "reader wouldn't notice and that Exact Match forgives.",
}

_test_types = sa.table(
    "test_types",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.Text()),
    sa.column("description", sa.Text()),
    sa.column("limitations", sa.Text()),
    sa.column("engine", sa.Text()),
    sa.column("engine_settings", sa.JSON()),
)


def _rewrite(rewrite) -> None:
    connection = op.get_bind()
    for row_id, name, engine_settings in connection.execute(
            sa.select(_test_types.c.id, _test_types.c.name, _test_types.c.engine_settings)
            .where(_test_types.c.engine.in_(_ENGINES))).all():
        connection.execute(_test_types.update().where(_test_types.c.id == row_id)
                           .values(engine_settings=rewrite(name, dict(engine_settings or {}))))


def _add(name: str, engine_settings: dict) -> dict:
    return {**engine_settings, _SETTING: name != _EXACT}


def _remove(name: str, engine_settings: dict) -> dict:
    engine_settings.pop(_SETTING, None)
    return engine_settings


def upgrade() -> None:
    _rewrite(_add)
    op.execute(_test_types.update().where(_test_types.c.name == _EXACT).values(**_EXACT_AFTER))


def downgrade() -> None:
    _rewrite(_remove)
    op.execute(_test_types.update().where(_test_types.c.name == _EXACT).values(**_EXACT_BEFORE))
