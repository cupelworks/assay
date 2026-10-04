"""The words the lists filter and sort by, the same on every list."""
from enum import StrEnum

from pydantic import BaseModel, Field


class VerdictFilter(StrEnum):
    """An item's newest batch's status, or `none` when it never ran with statistics."""
    pending = "Pending"
    running = "Running"
    passed = "Passed"
    failed = "Failed"
    inconclusive = "Inconclusive"
    incomplete = "Incomplete"
    not_ran = "NotRan"
    done = "Done"
    none = "none"


class RunFilter(StrEnum):
    """An item's newest run's status outside a batch, or `never`. For a test set
    or plan, its newest execution's: `Running` while any run is Pending or
    Running, else the first of NotRan, Red, Amber, Green its runs have."""
    pending = "Pending"
    running = "Running"
    green = "Green"
    amber = "Amber"
    red = "Red"
    not_ran = "NotRan"
    never = "never"


class TestSort(StrEnum):
    latest_activity = "latest_activity"
    name = "name"
    created = "created"
    dataset_row = "dataset_row"


class ScopeSort(StrEnum):
    latest_run = "latest_run"
    created = "created"
    name = "name"


class DatasetSort(StrEnum):
    newest = "newest"
    oldest = "oldest"
    name = "name"


class RowRange(StrEnum):
    """A dataset's size, by its rows: the `rows` filter's values and its facet's keys."""
    empty = "0"
    up_to_10 = "1-10"
    up_to_100 = "11-100"
    over_100 = "101+"


Counts = dict[str, int]
_CREATED = ("The created range by the edges sent (`created_edges`): each edge's count is what "
            "was created at or after it, `before` what was created before the earliest. "
            "Null without edges.")


class TestFacets(BaseModel):
    """The tests within the filters, counted by each filter's values: each
    facet within every other filter chosen, but not its own."""
    check_type: Counts = Field(description="By check type name.")
    latest_verdict: Counts = Field(description="By newest batch's status; `none`: no batch.")
    latest_run: Counts = Field(description="By newest run's status; `never`: no run.")
    has_recorded_answer: Counts = Field(description="`true` and `false`.")
    in_test_set: Counts = Field(description="`any`, `none`, and by test set id.")
    from_dataset: Counts = Field(description="By dataset id.")
    created: Counts | None = Field(default=None, description=_CREATED)


class TestSetFacets(BaseModel):
    """The test sets within the filters, counted like the tests."""
    latest_verdict: Counts
    latest_run: Counts = Field(description="By newest execution's outcome; `never`: none.")
    in_test_plan: Counts = Field(description="`any`, `none`, and by test plan id.")
    holds_test: Counts = Field(description="By the id of a test the set holds a copy of.")
    created: Counts | None = Field(default=None, description=_CREATED)


class TestPlanFacets(BaseModel):
    """The test plans within the filters, counted like the tests."""
    latest_verdict: Counts
    latest_run: Counts
    holds_test_set: Counts = Field(description="By the id of a test set the plan links.")
    created: Counts | None = Field(default=None, description=_CREATED)


class DatasetFacets(BaseModel):
    """The datasets within the filters, counted like the tests."""
    made_into_tests: Counts = Field(description="`true` and `false`.")
    rows: Counts = Field(description="By row count: `0`, `1-10`, `11-100`, `101+` (`rows`'s "
                                     "values).")
    created: Counts | None = Field(default=None, description=_CREATED)
