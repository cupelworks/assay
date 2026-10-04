import uuid
from dataclasses import dataclass

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import DatasetModel, DatasetRowModel, TestModel
from assay.schemas import (
    DatasetFacets,
    DataSetMetadata,
    DatasetSort,
    PaginatedDataSetResponse,
    RowRange,
)
from assay.services._listing import (
    Facet,
    ListFilters,
    Listing,
    contains_text,
    created_between,
    created_buckets,
)
from assay.services.datasets._common import _describe_datasets, _get_dataset_or_404

_ROW_COUNT = (select(func.count(DatasetRowModel.id))
              .where(DatasetRowModel.dataset_id == DatasetModel.id).scalar_subquery())

_ROW_RANGES = {RowRange.empty: _ROW_COUNT == 0,
               RowRange.up_to_10: _ROW_COUNT.between(1, 10),
               RowRange.up_to_100: _ROW_COUNT.between(11, 100),
               RowRange.over_100: _ROW_COUNT > 100}
"""Each row range's condition, shared by the `rows` filter and its facet."""

_FIRST_PROMPT = (select(DatasetRowModel.input)
                 .where(DatasetRowModel.dataset_id == DatasetModel.id)
                 .order_by(DatasetRowModel.position).limit(1)
                 .correlate(DatasetModel).scalar_subquery())
"""The prompt `first_prompt` shows: the lowest-numbered row's."""

_made_into_tests = exists().where(TestModel.dataset_row_id == DatasetRowModel.id,
                                  DatasetRowModel.dataset_id == DatasetModel.id)

DATASETS = Listing(
    key=DatasetModel.id,
    source=lambda statement: statement.select_from(DatasetModel),
    filters={
        "made_into_tests": lambda values: _made_into_tests if values[0] else ~_made_into_tests,
        "rows": lambda values: or_(*(_ROW_RANGES[value] for value in values)),
        "created": lambda values: created_between(DatasetModel.created_at, *values),
    },
    search=lambda q: contains_text(q, DatasetModel.name, _FIRST_PROMPT),
    sorts={DatasetSort.newest: (DatasetModel.created_at.desc(),),
           DatasetSort.oldest: (DatasetModel.created_at,),
           DatasetSort.name: (func.lower(DatasetModel.name),)},
    facets={
        "made_into_tests": Facet(buckets={"true": _made_into_tests,
                                          "false": ~_made_into_tests}),
        "rows": Facet(buckets={value.value: condition for value, condition in _ROW_RANGES.items()}),
    },
)


@dataclass(frozen=True)
class DatasetFilters(ListFilters):
    """The datasets list's filters, as the API received them."""
    made_into_tests: bool | None = None
    rows: list[RowRange] | None = None

    def chosen(self) -> dict:
        return {"made_into_tests": (None if self.made_into_tests is None
                                    else [self.made_into_tests]),
                "rows": self.rows, "created": self.created()}


async def get_datasets_metadata(offset: int, limit: int, session: AsyncSession,
                                filters: DatasetFilters | None = None,
                                sort: DatasetSort = DatasetSort.newest) -> PaginatedDataSetResponse:
    """The datasets within the filters, sorted and paged, each with its rows
    counted and its first prompt."""
    filters = filters or DatasetFilters()
    where = DATASETS.where(filters.chosen(), filters.q)
    datasets = list((await session.scalars(
        DATASETS.page(select(DatasetModel), where, sort, offset, limit))).all())
    return PaginatedDataSetResponse(items=await _describe_datasets(datasets, session),
                                    total=await DATASETS.count(session, where),
                                    offset=offset, limit=limit)


async def get_dataset_metadata_by_id(
        dataset_id: uuid.UUID,
        session: AsyncSession) -> DataSetMetadata:
    """Orchestrates single dataset retrieval: validates ID and returns its metadata.

    Args:
        dataset_id: The UUID of the dataset to retrieve.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The dataset metadata (id, name, created_at).

    Raises:
        HTTPException 404: No dataset exists with the given ID.
    """

    dataset = await _get_dataset_or_404(dataset_id, session)

    (described,) = await _describe_datasets([dataset], session)
    return described


async def get_dataset_facets(session: AsyncSession, filters: DatasetFilters | None = None,
                             created_edges: list[str] | None = None) -> DatasetFacets:
    """The datasets within the filters, counted by each filter's values."""
    filters = filters or DatasetFilters()
    extra = ({"created": Facet(buckets=created_buckets(DatasetModel.created_at, created_edges))}
             if created_edges else None)
    return DatasetFacets(**await DATASETS.facet_counts(session, filters.chosen(), filters.q,
                                                       extra))
