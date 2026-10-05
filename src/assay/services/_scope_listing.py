# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The test set and test plan lists' declarations, built by one factory:
the two differ only in their tables and their membership filters."""
from collections.abc import Callable, Mapping

from sqlalchemy import ColumnElement, func

from assay.models import (
    BatchStatus,
    StatisticalBatchModel,
    TestPlanExecutionModel,
    TestPlanModel,
    TestRunModel,
    TestSetExecutionModel,
    TestSetModel,
)
from assay.schemas import RunFilter, ScopeSort, VerdictFilter
from assay.services._listing import Facet, Listing, among, contains_text, created_between
from assay.services._standing import (
    execution_outcome_sql,
    latest_batch_sql,
    latest_execution_sql,
)


def scope_listing(model: type, execution_model: type, execution_scope: ColumnElement,
                  run_column: ColumnElement, batch_scope: ColumnElement,
                  filters: Mapping[str, Callable], facets: Mapping[str, Facet]) -> Listing:
    """A test set's or plan's list: by its newest batch's verdict and its newest
    execution's outcome, by `filters`, by creation, searched by name, sorted by
    latest run, creation or name; counted by the same and `facets`."""
    latest_execution = latest_execution_sql(execution_model, execution_scope, model.id,
                                            execution_model.id)
    latest_run_at = latest_execution_sql(execution_model, execution_scope, model.id,
                                         execution_model.created_at)
    verdict = latest_batch_sql(batch_scope, model.id, StatisticalBatchModel.status)
    outcome = execution_outcome_sql(run_column, latest_execution)
    return Listing(
        key=model.id,
        source=lambda statement: statement.select_from(model),
        filters={
            "latest_verdict": lambda values: among(verdict, values, "none", BatchStatus),
            "latest_run": lambda values: among(outcome, values, "never", str),
            "created": lambda values: created_between(model.created_at, *values),
            **filters,
        },
        search=lambda q: contains_text(q, model.name),
        sorts={ScopeSort.latest_run: (latest_run_at.is_(None), latest_run_at.desc(),
                                      model.created_at.desc()),
               ScopeSort.created: (model.created_at.desc(),),
               ScopeSort.name: (func.lower(model.name),)},
        facets={"latest_verdict": Facet(verdict, absent="none",
                                        values=[value.value for value in VerdictFilter]),
                "latest_run": Facet(outcome, absent="never",
                                    values=[value.value for value in RunFilter
                                            if value is not RunFilter.pending]),
                **facets},
    )


def set_listing(filters: Mapping[str, Callable], facets: Mapping[str, Facet]) -> Listing:
    return scope_listing(TestSetModel, TestSetExecutionModel, TestSetExecutionModel.test_set_id,
                         TestRunModel.test_set_execution_id, StatisticalBatchModel.test_set_id,
                         filters, facets)


def plan_listing(filters: Mapping[str, Callable], facets: Mapping[str, Facet]) -> Listing:
    return scope_listing(TestPlanModel, TestPlanExecutionModel,
                         TestPlanExecutionModel.test_plan_id, TestRunModel.test_plan_execution_id,
                         StatisticalBatchModel.test_plan_id, filters, facets)
