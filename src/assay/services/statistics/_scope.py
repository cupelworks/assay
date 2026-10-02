"""What a batch would run: a test, a test set or a test plan resolved into its
entries and their checks, with the run-creation guards applied, so the
estimate and the batch refuse exactly what an ordinary run would.
"""
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.assignment_labels import in_label_order
from assay.models import JUDGE_ENGINE, TestSetEntryModel, TestSetModel, TestTypesModel
from assay.schemas import TestTypeAssignment
from assay.schemas.statistics import Scope, ScopeKind, ScopeRequest
from assay.services.runs._common import (
    _check_test_set_entries_have_test_types_or_409,
    _check_tests_have_test_types_or_409,
    _find_test_plan_entries_or_409,
    _find_test_set_entries_ids_or_409,
    _find_test_sets_entries_ids_or_409,
)
from assay.services.statistics.compute import BatchEntry, entry_order, set_entry
from assay.services.test_plans._common import _find_test_plan_by_id_or_404
from assay.services.test_sets._common import _find_test_set_or_404
from assay.services.tests._common import (
    _assignment_schema,
    _find_all_tests_with_details_or_404,
)


@dataclass(frozen=True)
class ResolvedScope:
    scope: Scope
    entries: list[BatchEntry]
    # the catalogue row of every type the entries assign, by name
    types: dict[str, TestTypesModel] = field(default_factory=dict)

    @property
    def runs_per_time(self) -> int:
        return len(self.entries)

    def is_judge(self, assignment: TestTypeAssignment) -> bool:
        row = self.types.get(assignment.name)
        return row is not None and row.engine == JUDGE_ENGINE

    def is_scored(self, assignment: TestTypeAssignment) -> bool:
        """Scored against a threshold on a scale: its catalogue row declares a
        comparison (every NLP metric does; deterministic checks and judges
        don't)."""
        row = self.types.get(assignment.name)
        return row is not None and row.comparison is not None


async def resolve_scope(request: ScopeRequest, session: AsyncSession) -> ResolvedScope:
    """The scope's entries, in a stable order (by set name, then entry name),
    each with its checks in label order. 404 for an unknown test, set or
    plan; 409 for an empty set or plan, or an entry with no checks — the
    guards an ordinary run applies."""
    if request.test_id:
        (test,) = await _find_all_tests_with_details_or_404([request.test_id], session)
        _check_tests_have_test_types_or_409([test])
        assignments = in_label_order([_assignment_schema(a) for a in test.test_type_assignments])
        entries = [BatchEntry(
            entry_id=None, test_id=test.id, test_set_id=None, test_set_name=None,
            name=test.name, recorded_answer=test.model_output is not None,
            assignments=assignments,
        )]
        scope = Scope(kind=ScopeKind.test, id=test.id, name=test.name)
    elif request.test_set_id:
        test_set = await _find_test_set_or_404(request.test_set_id, session)
        entry_ids = await _find_test_set_entries_ids_or_409(test_set.id, session)
        await _check_test_set_entries_have_test_types_or_409(entry_ids, session)
        entries = await _entries(entry_ids, session)
        scope = Scope(kind=ScopeKind.test_set, id=test_set.id, name=test_set.name)
    else:
        test_plan = await _find_test_plan_by_id_or_404(request.test_plan_id, session)
        set_ids = await _find_test_plan_entries_or_409(test_plan.id, session)
        entry_ids = await _find_test_sets_entries_ids_or_409(set_ids, session)
        await _check_test_set_entries_have_test_types_or_409(entry_ids, session)
        entries = await _entries(entry_ids, session)
        scope = Scope(kind=ScopeKind.test_plan, id=test_plan.id, name=test_plan.name)

    names = {a.name for entry in entries for a in entry.assignments}
    types = {row.name: row for row in (await session.scalars(
        select(TestTypesModel).where(TestTypesModel.name.in_(names)))).all()}
    return ResolvedScope(scope=scope, entries=entries, types=types)


async def _entries(entry_ids: list[uuid.UUID], session: AsyncSession) -> list[BatchEntry]:
    rows = (await session.execute(
        select(TestSetEntryModel, TestSetModel.name)
        .join(TestSetModel, TestSetModel.id == TestSetEntryModel.test_set_id)
        .where(TestSetEntryModel.id.in_(set(entry_ids)))
    )).all()
    return sorted((set_entry(entry, set_name) for entry, set_name in rows), key=entry_order)
