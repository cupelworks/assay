# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Version 2's foundations on a real database: every run is created with its
check types, dataset rows carry their number, and every list has an order."""
import json
import uuid

from sqlalchemy import select

from assay.batch_waves import next_wave
from assay.models import (
    DatasetRowModel,
    StatisticalBatchModel,
    TestRunCheckTypeModel,
    TestRunModel,
)

CONTAINS = {"name": "Contains", "label": "Mentions it", "config": {"substring": "answer"}}
NOT_CONTAINS = {"name": "Does Not Contain", "config": {"substring": "sorry"}}


def _check_types(db, run_id) -> list[str]:
    with db.worker_session() as session:
        return sorted(session.scalars(select(TestRunCheckTypeModel.test_type_name)
                                      .where(TestRunCheckTypeModel.run_id == run_id)))


def _created(response) -> dict:
    assert response.status_code in (200, 201, 202), response.text
    return response.json()


def _runs_of_execution(db, execution_id) -> list[uuid.UUID]:
    execution_id = uuid.UUID(str(execution_id))
    with db.worker_session() as session:
        return list(session.scalars(select(TestRunModel.id).where(
            (TestRunModel.test_set_execution_id == execution_id)
            | (TestRunModel.test_plan_execution_id == execution_id))))


# --- every run is created with its check types ---


def test_a_standalone_run_records_its_check_types(db):
    test = db.test(checks=[CONTAINS, NOT_CONTAINS])

    run = _created(db.client.post(f"/runs/standalone/{test['id']}"))

    assert _check_types(db, uuid.UUID(run["id"])) == ["Contains", "Does Not Contain"]


def test_live_and_replayed_set_and_plan_runs_record_their_entrys_check_types(db):
    one, two = db.test(name="one", checks=[CONTAINS]), db.test(name="two",
                                                                checks=[NOT_CONTAINS])
    test_set = db.test_set("set", [one, two])
    plan = db.test_plan("plan", [test_set])

    live = _created(db.client.post(f"/runs/test-sets/{test_set['id']}"))
    replay = _created(db.client.post(
        f"/runs/test-sets/{test_set['id']}/executions/{live['id']}"))
    plan_live = _created(db.client.post(f"/runs/test-plans/{plan['id']}"))
    plan_replay = _created(db.client.post(
        f"/runs/test-plans/{plan['id']}/executions/{plan_live['id']}"))

    for execution in (live, replay, plan_live, plan_replay):
        types = sorted(tuple(_check_types(db, run_id))
                       for run_id in _runs_of_execution(db, execution["id"]))
        assert types == [("Contains",), ("Does Not Contain",)]


def test_a_batch_leaves_out_its_left_out_checks_types(db):
    test = db.test(checks=[CONTAINS, NOT_CONTAINS])

    batch = _created(db.client.post("/statistics/batches", json={
        "test_id": test["id"], "statistical_test": "binomial_gate",
        "leave_out": [{"label": "Does Not Contain"}]}))

    runs = db.runs_of(batch["id"])
    assert len(runs) == 29
    assert {tuple(_check_types(db, run.id)) for run in runs} == {("Contains",)}


def test_a_batchs_next_wave_records_check_types_too(db):
    test_set = db.test_set("set", [db.test(checks=[CONTAINS, NOT_CONTAINS])])
    batch = _created(db.client.post("/statistics/batches", json={
        "test_set_id": test_set["id"], "statistical_test": "sequential_gate", "times": 60,
        "leave_out": [{"entry_id": db.client.get(f"/test-sets/{test_set['id']}/entries")
                       .json()["items"][0]["id"], "label": "Mentions it"}]}))
    first_wave = len(db.runs_of(batch["id"]))

    with db.worker_session() as session:
        model = session.get(StatisticalBatchModel, uuid.UUID(batch["id"]))
        _, runs = next_wave(model, session, first_wave + 1, first_wave + 2)

    assert [[c.test_type_name for c in run.check_types] for run in runs] == [
        ["Does Not Contain"], ["Does Not Contain"]]


# --- dataset rows carry their number ---


def _import(db, tmp_path, name, prompts) -> dict:
    path = tmp_path / f"{name}.jsonl"
    path.write_text("".join(json.dumps({"prompt": p, "expected_output": "e",
                                        "model_output": "m"}) + "\n" for p in prompts))
    return _created(db.client.post("/datasets/path",
                                   json={"path": str(path), "dataset_name": name}))


def _numbers(db, dataset_id) -> list[tuple[str, int]]:
    with db.worker_session() as session:
        return [tuple(row) for row in session.execute(
            select(DatasetRowModel.input, DatasetRowModel.position)
            .where(DatasetRowModel.dataset_id == uuid.UUID(str(dataset_id)))
            .order_by(DatasetRowModel.position))]


def _row(prompt) -> dict:
    return {"prompt": prompt, "expected_output": "e", "model_output": "m"}


def test_an_import_numbers_its_lines_and_added_rows_go_after_the_highest(db, tmp_path):
    imported = _import(db, tmp_path, "d", ["a", "b", "c"])
    dataset_id = imported["dataset"]["id"]
    assert _numbers(db, dataset_id) == [("a", 1), ("b", 2), ("c", 3)]

    deleted = db.client.request("DELETE", "/datasets/rows",
                                json={"row_ids": [imported["loaded"]["ids"][2]]})
    assert deleted.status_code == 200, deleted.text
    _created(db.client.post("/datasets/rows", json={"id": dataset_id, "rows": [_row("d")]}))

    # d goes after the highest left (b's 2): c's tests lost their row when it
    # was deleted, so nothing still names 3
    assert _numbers(db, dataset_id) == [("a", 1), ("b", 2), ("d", 3)]


def test_a_deleted_rows_gap_is_kept(db, tmp_path):
    imported = _import(db, tmp_path, "d", ["a", "b", "c"])
    db.client.request("DELETE", "/datasets/rows",
                      json={"row_ids": [imported["loaded"]["ids"][1]]})

    assert _numbers(db, imported["dataset"]["id"]) == [("a", 1), ("c", 3)]


def test_replacing_the_content_numbers_the_new_rows_from_1(db, tmp_path):
    imported = _import(db, tmp_path, "d", ["a", "b", "c"])
    dataset_id = imported["dataset"]["id"]

    replaced = db.client.put("/datasets/rows",
                             json={"id": dataset_id, "rows": [_row("x"), _row("y")]})

    assert replaced.status_code == 200, replaced.text
    assert _numbers(db, dataset_id) == [("x", 1), ("y", 2)]


def test_rows_are_listed_by_their_number(db, tmp_path):
    imported = _import(db, tmp_path, "d", [f"p{i}" for i in range(12)])

    rows = db.client.get(f"/datasets/{imported['dataset']['id']}/rows",
                         params={"offset": 0, "limit": 100}).json()["items"]

    assert [row["row_info"]["prompt"] for row in rows] == [f"p{i}" for i in range(12)]


# --- every list has an order ---


def test_tests_test_sets_and_datasets_are_listed_newest_first(db, tmp_path):
    names = [db.test(name=f"t{i}")["name"] for i in range(3)]
    sets = [db.test_set(f"s{i}", [])["name"] for i in range(3)]
    datasets = [_import(db, tmp_path, f"d{i}", ["a"])["dataset"]["name"] for i in range(3)]

    assert [t["name"] for t in db.client.get("/tests").json()["test_cases"]] == names[::-1]
    assert [s["name"] for s in db.client.get("/test-sets").json()["items"]] == sets[::-1]
    assert [d["name"] for d in db.client.get("/datasets").json()["items"]] == datasets[::-1]
