# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Time in Assay, one rule for the API and the worker: every timestamp is
stored in UTC without an offset (the database columns hold none), and every
response sends it in UTC with one ("2026-09-27T12:36:59.928077Z"). UTC has no
daylight saving and doesn't depend on where the server runs, so stored
moments stay right when either changes."""
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator


def utc_now() -> datetime:
    """Now, as a timestamp is stored: UTC, without an offset."""
    return datetime.now(UTC).replace(tzinfo=None)


def as_utc(moment: datetime) -> datetime:
    """`moment` in UTC, with its offset: one without an offset is taken as UTC,
    as stored; one with an offset is converted."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def as_stored(moment: datetime) -> datetime:
    """`moment` as a timestamp is stored, to compare with stored ones: UTC,
    without an offset."""
    return as_utc(moment).replace(tzinfo=None)


Timestamp = Annotated[datetime, AfterValidator(as_utc)]
"""A timestamp in a response: always in UTC with its offset, `Z` in JSON."""
