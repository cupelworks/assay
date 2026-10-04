"""What the batches and comparisons lists share: filtering and counting by the
test, test set or test plan they ran. Both tables carry the three scope
columns, so the declarations are built per table."""
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import ColumnElement

from assay.services._listing import Facet

SCOPES = ("test_id", "test_set_id", "test_plan_id")


@dataclass(frozen=True)
class ScopeFilters:
    """The scopes asked for: several ids of one kind mean any of them;
    different kinds narrow together."""
    test_id: list[uuid.UUID] | None = None
    test_set_id: list[uuid.UUID] | None = None
    test_plan_id: list[uuid.UUID] | None = None

    def chosen(self) -> dict:
        return {scope: getattr(self, scope) for scope in SCOPES}


def _any_of(column: ColumnElement) -> Callable[[list], ColumnElement]:
    return lambda ids: column.in_(ids)


def scope_filters(model: type) -> dict[str, Callable[[list], ColumnElement]]:
    """A filter per scope column of `model`."""
    return {scope: _any_of(getattr(model, scope)) for scope in SCOPES}


def scope_facets(model: type) -> dict[str, Facet]:
    """A facet per scope column of `model`: counted by id, rows of other scopes
    (a null column) left out."""
    return {scope: Facet(getattr(model, scope)) for scope in SCOPES}
