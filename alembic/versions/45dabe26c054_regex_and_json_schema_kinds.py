"""regex and json_schema field kinds, and whole-number length bounds

Revision ID: 45dabe26c054
Revises: 3f8cecd2afff
Create Date: 2026-10-01 15:00:00.000000

Config values are now checked when a check is assigned, not first when a
run executes, so a typo is a 422 instead of a failed check that turns the
run Amber or Red. Most of that needs no data change: a `numeric` value is
now checked to parse and to lie within its field's min/max. What the rows
must say is which fields need more than that:

- Regex Match, Regex Full Match and Regex Must Not Match: `pattern`
  becomes kind `regex`, compiled on save.
- Matches JSON Schema: `schema` becomes kind `json_schema`, checked on
  save to be a valid JSON Schema, not only valid JSON.
- Word Count Limit and Character Count Limit: `max` and `min` get
  `integer: true`, so a fraction is refused. Their hints drop "A whole
  number", which the descriptor now says itself.

Rows are matched by engine, and for the JSON engine by its `check`
setting, so a row added by hand to one of these engines is updated too.
The downgrade restores the fields as they were.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '45dabe26c054'
down_revision: str | None = '3f8cecd2afff'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# hint before -> hint after, for the length bounds
_HINTS = {
    "A whole number; an answer of exactly this length meets it.":
        "An answer of exactly this length meets it.",
    "A whole number; spaces and newlines at the start or end aren't counted.":
        "Spaces and newlines at the start or end aren't counted.",
    "Optional. A whole number; leave it empty for no minimum.":
        "Optional. Leave it empty for no minimum.",
}
_HINTS_BACK = {after: before for before, after in _HINTS.items()}

_test_types = sa.table(
    "test_types",
    sa.column("id", sa.Uuid()),
    sa.column("engine", sa.Text()),
    sa.column("engine_settings", sa.JSON()),
    sa.column("config_fields", sa.JSON()),
)


def _upgrade_field(engine: str, engine_settings: dict, field: dict) -> dict:
    field = dict(field)
    if engine == "regex" and field["key"] == "pattern" and field["kind"] == "multiline":
        field["kind"] = "regex"
    elif (engine == "json" and engine_settings.get("check") == "schema"
          and field["key"] == "schema" and field["kind"] == "json"):
        field["kind"] = "json_schema"
    elif engine == "length" and field["key"] in ("max", "min"):
        field["integer"] = True
        field["hint"] = _HINTS.get(field.get("hint"), field.get("hint"))
    return field


def _downgrade_field(engine: str, engine_settings: dict, field: dict) -> dict:
    field = dict(field)
    if engine == "regex" and field["kind"] == "regex":
        field["kind"] = "multiline"
    elif engine == "json" and field["kind"] == "json_schema":
        field["kind"] = "json"
    elif engine == "length" and field["key"] in ("max", "min"):
        field.pop("integer", None)
        field["hint"] = _HINTS_BACK.get(field.get("hint"), field.get("hint"))
    return field


def _rewrite(rewrite_field) -> None:
    connection = op.get_bind()
    for row_id, engine, engine_settings, config_fields in connection.execute(
            sa.select(_test_types.c.id, _test_types.c.engine, _test_types.c.engine_settings,
                      _test_types.c.config_fields)
            .where(_test_types.c.engine.in_(("regex", "json", "length")))).all():
        fields = [rewrite_field(engine, engine_settings or {}, field)
                  for field in config_fields or []]
        connection.execute(_test_types.update().where(_test_types.c.id == row_id)
                           .values(config_fields=fields))


def upgrade() -> None:
    _rewrite(_upgrade_field)


def downgrade() -> None:
    _rewrite(_downgrade_field)
