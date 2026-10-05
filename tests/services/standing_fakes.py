# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Neutral standing for service tests on a mocked session: the describe
helpers' own mapping still runs, while the queries for how items stand
(tested on a real database in tests/test_standing.py) answer with nothing
and the counts a test gives."""
from contextlib import ExitStack, contextmanager
from datetime import datetime
from unittest.mock import patch

from assay.schemas import ScopeStanding, TestStanding

NO_SCOPE_STANDING = ScopeStanding(latest_batch=None, latest_execution=None, execution_count=0,
                                  has_runs=False)
_COMMONS = ("assay.services.test_sets._common", "assay.services.test_plans._common",
            "assay.services.datasets._common")
# the lists that bring batches up to date before filtering (tested on a real database)
_LISTS = ("assay.services.tests.get_tests", "assay.services.test_sets.get_test_sets_metadata",
          "assay.services.test_plans.get_test_plans_metadata")


def no_test_standing(created_at: datetime) -> TestStanding:
    return TestStanding(created_at=created_at, dataset_row_id=None, dataset_row_number=None,
                        latest_batch=None, latest_run=None, has_runs=False, copy_count=0,
                        test_set_count=0)


@contextmanager
def neutral_standing(counts: dict | None = None, default: int = 0):
    """Patch the standing queries: `count_by` answers `counts`, keyed by
    `(column, key)` or by key alone for any column (`default` otherwise);
    nothing has runs, nothing has a latest anything, no batch needs refreshing."""
    counts = counts or {}

    async def count_by(session, column, keys, *where, join=None):
        return {key: counts.get((column, key), counts.get(key, default)) for key in keys}

    async def scope_standing(session, model, ids):
        return dict.fromkeys(ids, NO_SCOPE_STANDING)

    async def test_standing(session, tests):
        return {test.id: no_test_standing(test.created_at if isinstance(test.created_at, datetime)
                                          else datetime(2026, 1, 1)) for test in tests}

    async def nothing(session, ids):
        return set()

    async def no_rows(*args, **kwargs):
        return {}

    async def no_refresh(*args, **kwargs):
        return None

    with ExitStack() as stack:
        for module in _COMMONS:
            stack.enter_context(patch(f"{module}.count_by", new=count_by))
        for module in _COMMONS[:2]:
            stack.enter_context(patch(f"{module}.scope_standing", new=scope_standing))
        stack.enter_context(patch("assay.services.test_sets._common.entries_with_runs",
                                  new=nothing))
        stack.enter_context(patch("assay.services.datasets._common.latest_per", new=no_rows))
        stack.enter_context(patch("assay.services.tests._common.test_standing",
                                  new=test_standing))
        for module in _LISTS:
            stack.enter_context(patch(f"{module}.refresh_batches", new=no_refresh))
        yield
