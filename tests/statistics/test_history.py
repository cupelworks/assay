"""The history the estimate plans from: per entry and check, the recent runs
that asked the same question and gave the check a result."""
import uuid

from assay.models import TestStatus
from assay.services.statistics import history as history_module
from assay.services.statistics.compute import BatchEntry

CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}
MISSES = {"name": "Contains", "label": "Misses", "config": {"substring": "absent"}}


def _histories(db, entries):
    """Load the history with a session of its own on the test's database."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    path = db.worker_session.kw["bind"].url.database

    async def load():
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        try:
            async with async_sessionmaker(engine)() as session:
                return await history_module.load_history(entries, session)
        finally:
            await engine.dispose()
    return asyncio.run(load())


def _set_runs(db, test_set, times):
    for _ in range(times):
        execution = db.client.post(f"/runs/test-sets/{test_set['id']}").json()
        runs = db.client.get(f"/runs/test-sets/{test_set['id']}/executions/"
                             f"{execution['id']}/test-runs").json()["items"]
        db.execute([uuid.UUID(run["id"]) for run in runs])


def _entry(db, test_set, name):
    entries = db.client.get(f"/test-sets/{test_set['id']}/entries").json()
    items = entries["items"] if isinstance(entries, dict) else entries
    found = next(e for e in items if e["name"] == name)
    return BatchEntry(entry_id=found["id"] if isinstance(found["id"], str) else found["id"],
                      test_id=None, test_set_id=test_set["id"], test_set_name="s", name=name,
                      recorded_answer=True, assignments=[])


def test_a_set_entrys_runs_are_its_history_check_by_check(db):
    test_set = db.test_set("s", [db.test(name="a", checks=[CONTAINS, MISSES]),
                                 db.test(name="b")])
    _set_runs(db, test_set, 3)
    a = _entry(db, test_set, "a")
    a.entry_id = uuid.UUID(str(a.entry_id))

    found = _histories(db, [a])

    assert {key[1]: (h.runs, h.passed) for key, h in found.items()} == {
        "Contains": (3, 3), "Misses": (3, 0)}
    assert all(key[0] == a.entry_id for key in found)  # entry b's runs never count


def test_errored_results_and_not_ran_runs_say_nothing(db):
    test = db.test(name="t")
    test_set = db.test_set("s", [test])
    _set_runs(db, test_set, 2)
    execution = db.client.post(f"/runs/test-sets/{test_set['id']}").json()
    run = db.client.get(f"/runs/test-sets/{test_set['id']}/executions/{execution['id']}"
                        "/test-runs").json()["items"][0]
    db.finish(uuid.UUID(run["id"]), {"Contains": {"passed": False, "errored": True,
                                       "detail": "Judge timed out"}})
    other = db.client.post(f"/runs/test-sets/{test_set['id']}").json()
    other_run = db.client.get(f"/runs/test-sets/{test_set['id']}/executions/{other['id']}"
                              "/test-runs").json()["items"][0]
    db.set_status(uuid.UUID(other_run["id"]), TestStatus.not_ran)
    entry = _entry(db, test_set, "t")
    entry.entry_id = uuid.UUID(str(entry.entry_id))

    (only,) = _histories(db, [entry]).values()

    assert (only.runs, only.passed) == (2, 2)


def test_only_the_most_recent_runs_count(db, monkeypatch):
    monkeypatch.setattr(history_module, "HISTORY_RUNS", 2)
    test_set = db.test_set("s", [db.test(name="t")])
    _set_runs(db, test_set, 4)
    entry = _entry(db, test_set, "t")
    entry.entry_id = uuid.UUID(str(entry.entry_id))

    (only,) = _histories(db, [entry]).values()

    assert only.runs == 2


def test_a_standalone_tests_runs_before_an_edit_dont_count(db):
    test = db.test(name="t")
    for _ in range(3):
        run = db.client.post(f"/runs/standalone/{test['id']}").json()
        db.execute([uuid.UUID(run["id"])])
    db.client.patch(f"/tests/{test['id']}", json={"input": "a new question"})
    run = db.client.post(f"/runs/standalone/{test['id']}").json()
    db.execute([uuid.UUID(run["id"])])
    entry = BatchEntry(entry_id=None, test_id=uuid.UUID(test["id"]), test_set_id=None,
                       test_set_name=None, name="t", recorded_answer=True, assignments=[])

    (only,) = _histories(db, [entry]).values()

    assert (only.runs, only.passed) == (1, 1)


def test_a_scored_check_keeps_its_scores():
    check = history_module.CheckHistory()
    for result in ({"passed": True, "score": 0.6}, {"passed": False, "score": 0.4},
                   {"passed": True, "score": None}, {"passed": None}):
        check.add(result)

    assert (check.runs, check.passed, check.scores) == (3, 2, [0.6, 0.4])
