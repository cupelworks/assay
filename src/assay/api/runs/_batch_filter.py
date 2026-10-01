"""The `?batch=` filter every run and execution listing takes."""
from typing import Annotated

from fastapi import Query

BatchFilter = Annotated[str | None, Query(
    pattern=r"^(none|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$",
    description=(
        "Runs and executions created by a statistical batch (`POST /statistics/batches`) are "
        "listed with everything else, each with its `batch_id`. `none` keeps only what no "
        "batch created (what was run once or replayed — a 46-times batch would otherwise add "
        "46 rows); a batch's id keeps only that batch's. Omit for everything."
    ),
    examples=["none"],
)]
