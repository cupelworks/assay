# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The `?batch=` filter every run and execution listing takes."""
from typing import Annotated

from fastapi import Query

from assay.api._filters import UUID_PATTERN

BatchFilter = Annotated[str | None, Query(
    pattern=rf"^(none|{UUID_PATTERN})$",
    description=(
        "Runs and executions created by a statistical batch (`POST /statistics/batches`) are "
        "listed with everything else, each with its `batch_id`. `none` keeps only what no "
        "batch created (what was run once or replayed — a 46-times batch would otherwise add "
        "46 rows); a batch's id keeps only that batch's. Omit for everything."
    ),
    examples=["none"],
)]
