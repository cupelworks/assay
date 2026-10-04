"""One way to filter, search, sort, page and count a list, declared once per
list.

A `Listing` names what a list counts (its key), where its rows come from (the
table and its joins), its filters, its search, its sorts and its facets. The
list endpoint and its facets endpoint read the same declaration, so a filter
is written once and narrows both the same way.

Filters follow one convention: a filter given several times means any of its
values, and different filters narrow together. Search takes phrases: each is
a case-insensitive substring that must be found in one of the searched
fields, all of them in any order, the same on SQLite and Postgres.
"""
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from sqlalchemy import ColumnElement, Select, and_, case, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.timestamps import as_stored

Values = Sequence


def _identity(statement: Select) -> Select:
    return statement


@dataclass(frozen=True)
class ListFilters:
    """What every list filters by: a created range, and search phrases."""
    created_from: datetime | None = None
    created_to: datetime | None = None
    q: list[str] | None = None

    def created(self) -> tuple | None:
        return ((self.created_from, self.created_to)
                if self.created_from or self.created_to else None)


@dataclass(frozen=True)
class Facet:
    """How one filter's values are counted. Grouped: the expression grouped on,
    the joins it needs beyond the list's own, the values always reported (0
    when nothing has them), and the name a missing value is reported as.
    Buckets: named conditions, each counted. A facet can have both."""
    value: ColumnElement | None = None
    join: Callable[[Select], Select] = _identity
    values: Sequence[str] = ()
    absent: str | None = None
    buckets: Mapping[str, ColumnElement] = field(default_factory=dict)


@dataclass(frozen=True)
class Listing:
    key: ColumnElement
    source: Callable[[Select], Select]
    filters: Mapping[str, Callable[[Values], ColumnElement]] = field(default_factory=dict)
    search: Callable[[str], ColumnElement] | None = None
    sorts: Mapping[str, Sequence[ColumnElement]] = field(default_factory=dict)
    facets: Mapping[str, Facet] = field(default_factory=dict)

    def where(self, chosen: Mapping[str, Values | None], q: Sequence[str] | None = None,
              without: str | None = None) -> list[ColumnElement]:
        """The clauses for the chosen filters and search; `without` leaves one
        filter out, for its own facet. Each phrase of `q` must be found."""
        clauses = [self.filters[name](values) for name, values in chosen.items()
                   if name != without and values]
        if self.search is not None:
            clauses.extend(self.search(phrase.strip()) for phrase in q or [] if phrase.strip())
        return clauses

    async def count(self, session: AsyncSession, where: Sequence[ColumnElement]) -> int:
        return await session.scalar(
            self.source(select(func.count(distinct(self.key)))).where(*where)) or 0

    def page(self, columns: Select, where: Sequence[ColumnElement], sort: str,
             offset: int, limit: int) -> Select:
        """`columns` (a select without a FROM) over the list's rows, filtered,
        sorted by `sort` then the key, and paged."""
        return (self.source(columns).where(*where).order_by(*self.sorts[sort], self.key)
                .offset(offset).limit(limit))

    async def facet_counts(self, session: AsyncSession, chosen: Mapping[str, Values | None],
                           q: Sequence[str] | None = None,
                           extra: Mapping[str, Facet] | None = None) -> dict[str, dict[str, int]]:
        """Each facet's values counted within every other filter chosen (a
        facet is named as the filter it counts). `extra` adds facets built for
        this request, the created range's edges, say."""
        counts = {}
        for name, facet in {**self.facets, **(extra or {})}.items():
            where = self.where(chosen, q, without=name)
            found = dict.fromkeys(facet.values, 0)
            if facet.value is not None:
                statement = (facet.join(self.source(select(facet.value,
                                                           func.count(distinct(self.key)))))
                             .where(*where).group_by(facet.value))
                for value, n in (await session.execute(statement)).all():
                    if value is not None or facet.absent:
                        found[facet.absent if value is None else _text(value)] = n
            if facet.buckets:
                names = list(facet.buckets)
                row = (await session.execute(self.source(select(*(
                    func.count(distinct(case((facet.buckets[bucket], self.key))))
                    for bucket in names))).where(*where))).one()
                found.update(zip(names, row, strict=True))
            counts[name] = found
        return counts


def _text(value) -> str:
    return value.value if isinstance(value, Enum) else str(value)


def contains_text(q: str, *columns: ColumnElement) -> ColumnElement:
    """Any of the columns contains `q`, ignoring case (`%` and `_` in `q` are
    matched as themselves)."""
    needle = q.lower()
    return or_(*(func.lower(column).contains(needle, autoescape=True) for column in columns))


def created_between(column: ColumnElement, start: datetime | None,
                    end: datetime | None) -> ColumnElement | None:
    """`start <= column < end`, either side open when left out; None when both are."""
    bounds = [clause for clause in ((column >= as_stored(start)) if start else None,
                                    (column < as_stored(end)) if end else None)
              if clause is not None]
    return and_(*bounds) if bounds else None


def among(expression: ColumnElement, values: Sequence, absent: str,
          stored: Callable = lambda value: value) -> ColumnElement:
    """`expression` is one of `values` (each turned into its stored form by
    `stored`); the value `absent` stands for no value at all (null)."""
    present = [stored(value) for value in values if value != absent]
    clauses = [expression.in_(present)] if present else []
    if absent in values:
        clauses.append(expression.is_(None))
    return or_(*clauses)


def membership(values: Sequence[str], related: Callable[[list | None], ColumnElement]
               ) -> ColumnElement:
    """`any`: related to something; `none`: to nothing; ids: to one of them.
    `related(None)` says "related to something", `related(ids)` "to one of these"."""
    ids = [uuid.UUID(value) for value in values if value not in ("any", "none")]
    clauses = [related(ids)] if ids else []
    if "any" in values:
        clauses.append(related(None))
    if "none" in values:
        clauses.append(~related(None))
    return or_(*clauses)


def created_buckets(column: ColumnElement, edges: Sequence[str]) -> dict[str, ColumnElement]:
    """The created facet's buckets for the moments `edges` (ISO 8601, as sent):
    each counts what was created at or after it, and `before` what was created
    before the earliest. The keys are the edges as sent."""
    moments = {edge: datetime.fromisoformat(edge) for edge in edges}
    buckets = {edge: column >= as_stored(moment) for edge, moment in moments.items()}
    if moments:
        buckets["before"] = column < as_stored(min(moments.values(), key=as_stored))
    return buckets
