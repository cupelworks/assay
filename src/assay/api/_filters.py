"""The query parameters every list shares, declared once: paging, a created
range, search phrases, and the status and membership filters. A filter given
several times means any of its values; different filters narrow together."""
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Query
from pydantic import AfterValidator, StringConstraints

from assay.schemas import RunFilter, VerdictFilter

Offset = Annotated[int, Query(ge=0, description="Number of records to skip.")]
Limit = Annotated[int, Query(ge=1, le=500, description="Maximum number of records (1–500).")]

CreatedFrom = Annotated[datetime | None, Query(
    description="Only what was created at or after this moment (ISO 8601: with an offset, or "
                "taken as UTC without one).")]
CreatedTo = Annotated[datetime | None, Query(
    description="Only what was created before this moment.")]


def search(fields: str) -> object:
    """The `q` parameter of a list searching `fields`."""
    return Query(description=f"Phrases searched, ignoring case, in {fields}; give it once per "
                             "phrase, and each must be found (in any order).")


Verdicts = Annotated[list[VerdictFilter] | None, Query(
    description="Only items whose newest statistical batch has one of these statuses; `none` "
                "means never run with statistics.")]
Runs = Annotated[list[RunFilter] | None, Query(
    description="Only items whose newest run outside a batch has one of these statuses; "
                "`never` means never run. For a test set or plan, its newest execution's: "
                "`Running` while any run is Pending or Running, else the first of NotRan, "
                "Red, Amber, Green its runs have.")]

UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_MEMBERSHIP = rf"^(any|none|{UUID_PATTERN})$"
Membership = list[Annotated[str, StringConstraints(pattern=_MEMBERSHIP)]] | None
Ids = list[uuid.UUID] | None


def _a_moment(edge: str) -> str:
    datetime.fromisoformat(edge)  # a ValueError is a 422 naming the edge
    return edge


CreatedEdges = Annotated[list[Annotated[str, AfterValidator(_a_moment)]] | None, Query(
    description="The created facet's edges (ISO 8601 moments, the FE's own day boundaries): "
                "each is counted as what was created at or after it, keyed by the edge as "
                "sent, and `before` counts what was created before the earliest.")]
