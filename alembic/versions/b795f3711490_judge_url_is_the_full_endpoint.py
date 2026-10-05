# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""the judge's URL is the full endpoint, called as written

Revision ID: b795f3711490
Revises: 66000c4b71ab
Create Date: 2026-10-01 10:00:00.000000

`base_url` was an API root the worker appended a path to (`/v1/messages`,
`/chat/completions`), so the URL a user typed wasn't the URL called. It
becomes `url`: the whole endpoint, never rewritten; null still means the
provider's own. Saved judge settings and the settings stored on past judge
checks are rewritten to the new key with today's path appended, so each
keeps calling exactly what it called before. The downgrade strips the path
back off where it's the one that was appended.

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b795f3711490'
down_revision: str | None = '66000c4b71ab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PATHS = {"anthropic": "/v1/messages", "openai": "/chat/completions"}

_settings = sa.table("settings", sa.column("section", sa.Text()),
                     sa.column("value", sa.JSON()))
_checks = sa.table("judge_checks", sa.column("id", sa.Uuid()),
                   sa.column("settings", sa.JSON()))


def _to_url(value: dict) -> dict:
    value = dict(value)
    base_url = value.pop("base_url", None)
    path = _PATHS.get(value.get("provider"), "")
    value["url"] = f"{base_url.rstrip('/')}{path}" if base_url else None
    return value


def _to_base_url(value: dict) -> dict:
    value = dict(value)
    url = value.pop("url", None)
    path = _PATHS.get(value.get("provider"), "")
    if url and path and url.endswith(path):
        url = url[:-len(path)]
    value["base_url"] = url
    return value


def _rewrite(rewrite) -> None:
    connection = op.get_bind()
    for section, value in connection.execute(
            sa.select(_settings.c.section, _settings.c.value)
            .where(_settings.c.section == "judge")).all():
        connection.execute(_settings.update().where(_settings.c.section == section)
                           .values(value=rewrite(value)))
    for check_id, settings in connection.execute(
            sa.select(_checks.c.id, _checks.c.settings)).all():
        connection.execute(_checks.update().where(_checks.c.id == check_id)
                           .values(settings=rewrite(settings)))


def upgrade() -> None:
    _rewrite(_to_url)


def downgrade() -> None:
    _rewrite(_to_base_url)
