# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Which application-under-test settings are in effect, and where from.

The group's row in the settings table, when there is one, is the whole
truth; with no row, the ASSAY_TARGET_* environment and the code defaults
are. Shared by the API (GET/PATCH/DELETE /settings/target, checks) and the
worker (every run that calls the application), each fetching the row with
its own kind of session — this only decides what the row, or its absence,
means.
"""
from assay.config import settings
from assay.models import SettingsModel
from assay.schemas.settings import SettingsSource, TargetSettingsRead


def resolve_target_settings(row: SettingsModel | None) -> TargetSettingsRead:
    """The effective settings for the `target` group.

    Args:
        row: The group's row in the settings table, or None if it has none.

    Raises:
        pydantic.ValidationError: the stored row no longer satisfies the
            rules (a manual edit, or a later release tightening one) — a
            bug to fix with a migration, never silently replaced by the
            environment's values.
    """
    if row is None:
        return TargetSettingsRead(
            **settings.target_settings().model_dump(),
            source=SettingsSource.environment,
            updated_at=None,
        )
    return TargetSettingsRead(
        **row.value,
        source=SettingsSource.database,
        updated_at=row.updated_at,
    )
