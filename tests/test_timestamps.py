# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Every timestamp is stored in UTC and sent in UTC with its offset."""
from datetime import UTC, datetime, timedelta, timezone

from pydantic import BaseModel

from assay.timestamps import Timestamp, as_stored, as_utc, utc_now
from tests.timestamp_checks import without_offset

CONTAINS = [{"name": "Contains", "config": {"substring": "answer"}}]


# --- the helpers ---


def test_now_is_utc_without_an_offset():
    now = utc_now()

    assert now.tzinfo is None
    assert abs(now - datetime.now(UTC).replace(tzinfo=None)) < timedelta(seconds=1)


def test_a_moment_without_an_offset_is_utc_and_one_with_is_converted():
    rome_summer = timezone(timedelta(hours=2))

    assert as_utc(datetime(2026, 7, 1, 12, 0)) == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert as_utc(datetime(2026, 7, 1, 14, 0, tzinfo=rome_summer)) == datetime(
        2026, 7, 1, 12, 0, tzinfo=UTC)
    assert as_stored(datetime(2026, 7, 1, 14, 0, tzinfo=rome_summer)) == datetime(
        2026, 7, 1, 12, 0)


def test_a_response_timestamp_is_sent_in_utc_with_z():
    class Read(BaseModel):
        at: Timestamp

    stored = Read(at=datetime(2026, 9, 27, 12, 36, 59, 928077)).model_dump_json()
    offset = Read(at="2026-10-01T09:52:41+02:00").model_dump_json()

    assert stored == '{"at":"2026-09-27T12:36:59.928077Z"}'
    assert offset == '{"at":"2026-10-01T07:52:41Z"}'


def test_the_check_finds_a_timestamp_without_an_offset():
    assert without_offset({"a": [{"at": "2026-09-27T14:36:59.928077"}],
                           "b": "2026-09-27T12:36:59Z", "c": "2026-09-27"}) == [
        ".a[0].at = 2026-09-27T14:36:59.928077"]


# --- every response, on a real database ---


def _get(db, path: str, **params):
    response = db.client.get(path, params=params)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def test_every_response_sends_its_timestamps_in_utc_with_an_offset(db):
    test = db.test(checks=CONTAINS)
    test_set = db.test_set("Answers", [test])
    plan = db.test_plan("Release", [test_set])
    db.client.post(f"/runs/standalone/{test['id']}")
    db.client.post(f"/runs/test-sets/{test_set['id']}")
    db.client.post(f"/runs/test-plans/{plan['id']}")
    db.execute(db.dispatched)
    batches = [db.client.post("/statistics/batches", json={
        "statistical_test": "binomial_gate", "test_id": test["id"]}).json() for _ in range(2)]
    for batch in batches:
        db.execute([run.id for run in db.runs_of(batch["id"])])
    comparison = db.client.post("/statistics/comparisons", json={
        "batch_a": batches[0]["id"], "batch_b": batches[1]["id"]}).json()
    db.client.patch("/settings/target", json={"url": "http://localhost:9/answer"})
    standalone = _get(db, f"/runs/standalone/{test['id']}/test-runs")["items"][0]
    execution = _get(db, f"/runs/test-sets/{test_set['id']}/executions")["items"][0]["id"]
    set_runs = f"/runs/test-sets/{test_set['id']}/executions/{execution}/test-runs"

    responses = {path: _get(db, path) for path in (
        "/tests", f"/tests/{test['id']}", "/test-sets", f"/test-sets/{test_set['id']}/entries",
        "/test-plans", f"/test-plans/{plan['id']}/entries", "/runs", "/runs/executions",
        f"/runs/standalone/{test['id']}/test-runs/{standalone['id']}",
        f"/runs/test-sets/{test_set['id']}/executions/{execution}", set_runs,
        f"{set_runs}/{_get(db, set_runs)['items'][0]['id']}",
        "/statistics/batches", f"/statistics/batches/{batches[0]['id']}",
        f"/statistics/comparisons/{comparison['id']}", "/settings/target", "/health/worker")}

    for path, body in responses.items():
        assert without_offset(body) == [], path
    created = datetime.fromisoformat(responses[f"/tests/{test['id']}"]["created_at"])
    assert created.utcoffset() == timedelta(0)
    assert abs(created - datetime.now(UTC)) < timedelta(minutes=1)  # stored in UTC
