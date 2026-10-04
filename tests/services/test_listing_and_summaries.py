import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, MetaData, String, Table
from sqlalchemy.dialects import sqlite

from assay.models import TestStatus
from assay.services._listing import contains_text, created_between
from assay.services.runs._summaries import count_checks, execution_checks

# --- the check counter ---


def test_a_check_is_met_when_it_passed_and_didnt_error():
    checks = count_checks({
        "Says answer": {"passed": True, "errored": False},
        "Says refund": {"passed": False, "errored": False},
        "Toxicity": {"passed": False, "errored": True},
    })

    assert (checks.met, checks.total, checks.not_met) == (1, 3, ["Says refund", "Toxicity"])


def test_a_run_without_results_has_no_checks_yet():
    assert count_checks(None) is None
    assert count_checks({}).model_dump() == {"met": 0, "total": 0, "not_met": []}


def _run(status, results=None, error=None, name="t", assignments=(), skip_labels=None):
    return SimpleNamespace(id=uuid.uuid4(), status=status, results=results, error=error,
                           test_name=name, assignments=list(assignments), skip_labels=skip_labels)


def test_an_executions_checks_count_finished_runs_and_list_what_wasnt_met_or_run():
    runs = [
        _run(TestStatus.amber, {"A": {"passed": True}, "B": {"passed": False}}, name="One"),
        _run(TestStatus.green, {"A": {"passed": True}}, name="Two"),
        _run(TestStatus.not_ran, error="No answer", name="Three",
             assignments=[{"name": "Contains", "label": "A"}, {"name": "ROUGE", "label": "B"},
                          {"name": "Toxicity", "label": "C"}], skip_labels=["C"]),
        _run(TestStatus.running, name="Four"),
    ]

    checks = execution_checks(runs)

    assert checks.met == 2
    assert [(c.test_name, c.label) for c in checks.not_met] == [("One", "B")]
    assert [(c.test_name, c.error, c.checks) for c in checks.not_ran] == [
        ("Three", "No answer", 2)]


# --- the list-query helpers ---

_TABLE = Table("things", MetaData(), Column("name", String), Column("created_at", DateTime))


def _sql(clause) -> str:
    return str(clause.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))


def test_search_lowers_both_sides_and_matches_wildcards_as_themselves():
    sql = _sql(contains_text("50%_Off", _TABLE.c.name))

    assert "lower(things.name)" in sql
    assert "'%' || '50/%/_off' || '%'" in sql and "ESCAPE '/'" in sql


def test_a_range_can_be_open_on_either_side():
    start, end = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC)

    assert created_between(_TABLE.c.created_at, None, None) is None
    assert ">=" in _sql(created_between(_TABLE.c.created_at, start, None))
    both = _sql(created_between(_TABLE.c.created_at, start, end))
    assert ">=" in both and "<" in both and " AND " in both
