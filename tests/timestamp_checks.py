# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Finds timestamps sent without an offset: RFC 3339, the spec's `date-time`,
requires one."""
import re

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_OFFSET = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def without_offset(value, path: str = "") -> list[str]:
    """The paths of every timestamp string in `value` (any depth) that has no
    offset."""
    if isinstance(value, dict):
        return [found for key, item in value.items()
                for found in without_offset(item, f"{path}.{key}")]
    if isinstance(value, list):
        return [found for n, item in enumerate(value)
                for found in without_offset(item, f"{path}[{n}]")]
    if isinstance(value, str) and _TIMESTAMP.match(value) and not _OFFSET.search(value):
        return [f"{path} = {value}"]
    return []
