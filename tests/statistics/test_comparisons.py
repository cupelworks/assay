"""Comparisons end to end on a real database."""
from assay.models import TestStatus

CONTAINS = {"name": "Contains", "config": {"substring": "answer"}}


def _batch(db, **scope) -> dict:
    response = db.client.post("/statistics/batches",
                              json={"statistical_test": "binomial_gate", **scope})
    assert response.status_code == 202, response.text
    return response.json()


def _finish(db, batch) -> None:
    db.execute([run.id for run in db.runs_of(batch["id"])])


def _compare(db, a, b, **body):
    return db.client.post("/statistics/comparisons",
                          json={"batch_a": a["id"], "batch_b": b["id"], **body})


def test_a_changed_recorded_answer_is_compared_and_stored(db):
    test = db.test(checks=[CONTAINS])
    a = _batch(db, test_id=test["id"], note="v1")
    _finish(db, a)
    db.client.patch(f"/tests/{test['id']}", json={"model_output": "nothing useful"})
    b = _batch(db, test_id=test["id"], note="v2")
    _finish(db, b)

    response = _compare(db, a, b, note="  v2 against v1 ")

    assert response.status_code == 201, response.text
    comparison = response.json()
    assert comparison["note"] == "v2 against v1"
    assert (comparison["batch_a"]["note"], comparison["batch_b"]["note"]) == ("v1", "v2")
    check = comparison["result"]["entries"][0]["checks"][0]
    assert check["verdict"] == "worse"
    assert (check["a"]["pass_rate"]["point"], check["b"]["pass_rate"]["point"]) == (1.0, 0.0)
    assert comparison["summary"] == "B is worse on the check."
    read = db.client.get(f"/statistics/comparisons/{comparison['id']}").json()
    assert read == comparison
    bare = db.client.get(f"/statistics/comparisons/{comparison['id']}",
                         params={"series": "false"}).json()
    assert bare["result"]["entries"][0]["checks"][0]["a"]["series"] is None
    listed = db.client.get("/statistics/comparisons", params={"batch_id": a["id"]}).json()
    assert [c["id"] for c in listed["items"]] == [comparison["id"]]
    assert "result" not in listed["items"][0]
    by_scope = db.client.get("/statistics/comparisons", params={"test_id": test["id"]}).json()
    assert by_scope["total"] == 1


def test_a_running_batch_cant_be_compared(db):
    test = db.test()
    a, b = _batch(db, test_id=test["id"]), _batch(db, test_id=test["id"])
    _finish(db, a)
    db.set_status(db.runs_of(b["id"])[0].id, TestStatus.running)

    response = _compare(db, a, b)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Only finished batches can be compared: batch B is Running")


def test_batches_of_different_scopes_are_refused(db):
    a = _batch(db, test_id=db.test(name="one")["id"])
    b = _batch(db, test_id=db.test(name="two")["id"])
    _finish(db, a)
    _finish(db, b)

    response = _compare(db, a, b)

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == (
        "Batch B ran test 'two', batch A test 'one': compare two batches of the same scope")


def test_a_test_whose_checks_changed_between_batches_is_refused(db):
    test = db.test(checks=[CONTAINS])
    a = _batch(db, test_id=test["id"])
    _finish(db, a)
    db.client.patch(f"/tests/{test['id']}", json={"test_type_assignments": [
        {"name": "Contains", "config": {"substring": "ans"}}]})
    b = _batch(db, test_id=test["id"])
    _finish(db, b)

    response = _compare(db, a, b)

    assert response.status_code == 422
    assert "the checks changed" in response.json()["detail"][0]["msg"]


def test_the_same_batch_twice_or_a_batch_test_is_a_422(db):
    test = db.test()
    a = _batch(db, test_id=test["id"])

    assert _compare(db, a, a).status_code == 422
    other = _batch(db, test_id=test["id"])
    response = _compare(db, a, other, statistical_test="binomial_gate")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "statistical_test"]


def test_an_unknown_batch_or_comparison_is_a_404(db):
    test = db.test()
    a = _batch(db, test_id=test["id"])

    missing = {"id": "00000000-0000-0000-0000-000000000000"}
    assert _compare(db, a, missing).status_code == 404
    assert db.client.get(f"/statistics/comparisons/{missing['id']}").status_code == 404


def test_an_entry_added_to_the_set_between_batches_is_unmatched(db):
    test_set = db.test_set("support", [db.test(name="kept")])
    a = _batch(db, test_set_id=test_set["id"])
    _finish(db, a)
    db.client.post(f"/test-sets/{test_set['id']}/entries", json=[{"id": db.test(name="new")["id"]}])
    b = _batch(db, test_set_id=test_set["id"])
    _finish(db, b)

    result = _compare(db, a, b).json()["result"]

    assert [e["name"] for e in result["entries"]] == ["kept"]
    assert [(u["name"], u["label"], u["only_in"]) for u in result["unmatched"]] == [
        ("new", None, "b")]
