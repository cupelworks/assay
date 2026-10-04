"""say in the rubric hint of the judges that never see the expected output

Revision ID: 66000c4b71ab
Revises: 1e4b81f30147
Create Date: 2026-10-01 08:00:00.000000

Relevance, Bias and Toxicity judge the answer on its own: the expected
output is never sent to the judge for them, whatever a custom rubric says.
Their rubric field's hint now says so, so nobody writes a rubric that
compares with something the judge won't see. The downgrade restores the
shared hint.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '66000c4b71ab'
down_revision: str | None = '1e4b81f30147'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROWS = ("Relevance", "Bias", "Toxicity")
_HINT = ("Optional. Replaces the default rubric, shown as the example; say what the answer "
         "must do to pass.")
_NO_REFERENCE = (" This type never sees the expected output, so the rubric can't compare "
                 "with it.")

_test_types = sa.table(
    "test_types",
    sa.column("name", sa.Text()),
    sa.column("config_fields", sa.JSON()),
)


def _set_rubric_hint(hint: str) -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(_test_types.c.name, _test_types.c.config_fields)
        .where(_test_types.c.name.in_(_ROWS))
    ).all()
    for name, fields in rows:
        fields = [{**field, "hint": hint} if field["key"] == "rubric" else field
                  for field in fields]
        connection.execute(_test_types.update().where(_test_types.c.name == name)
                           .values(config_fields=fields))


def upgrade() -> None:
    _set_rubric_hint(_HINT + _NO_REFERENCE)


def downgrade() -> None:
    _set_rubric_hint(_HINT)
