# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The check types a run asks, written when the run is created: its frozen
copy's checks, without the labels a statistical batch left out. One row per
type (`TestRunCheckTypeModel`), so runs can be filtered by check type with a
plain join on every database.

Shared by the API, which creates runs, and the worker, which creates a
batch's later waves; each loads the entries' frozen checks with its own
session and hands them over.
"""
import uuid
from collections.abc import Iterable, Mapping

from assay.models import TestRunCheckTypeModel, TestRunModel


def asked_checks(assignments: Iterable[Mapping], skip_labels: Iterable[str] | None = None
                 ) -> list[Mapping]:
    """The checks a run asks: its frozen assignments (`{"name", "label", …}`)
    without those whose label a statistical batch left out."""
    skipped = set(skip_labels or [])
    return [a for a in assignments or [] if a.get("label") not in skipped]


def check_types(assignments: Iterable[Mapping], skip_labels: Iterable[str] | None = None
                ) -> list[str]:
    """The distinct check type names a run asks, sorted."""
    return sorted({a["name"] for a in asked_checks(assignments, skip_labels)})


def with_check_types(runs: Iterable[TestRunModel],
                     entry_assignments: Mapping[uuid.UUID, list[dict]] | None = None) -> None:
    """Give each run its check types: from its standalone copy, else from its
    entry's frozen checks in `entry_assignments`. Call it once the runs' skip
    labels are set; it replaces any check types they had."""
    for run in runs:
        copy = run.standalone_run
        assignments = (copy.test_type_assignments if copy is not None
                       else (entry_assignments or {})[run.test_set_entry_id])
        run.check_types = [TestRunCheckTypeModel(run_id=run.id, test_type_name=name)
                           for name in check_types(assignments, run.skip_labels)]
