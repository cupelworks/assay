"""The catalogue of statistical tests as rows: what the seed gives, what editing
a row changes, what a row the code can't run does, and what the engine
recorded on a batch protects."""
import pytest
from sqlalchemy import delete, insert, select, update

from assay.models import StatisticalTestModel
from assay.services.statistics.catalogue import CatalogueError, entry_of

ENGINES = ["binomial_gate", "one_sample_t", "judge_stability", "trial", "sequential_gate",
           "sequential_judge_stability", "pass_rates", "no_worse", "mean_scores",
           "score_ranks", "paired_entries"]


def _row(db, test_id: str) -> StatisticalTestModel:
    with db.worker_session() as session:
        return session.scalar(
            select(StatisticalTestModel).where(StatisticalTestModel.id == test_id))


def _edit(db, test_id: str, **values) -> None:
    with db.worker_session() as session:
        session.execute(update(StatisticalTestModel).where(StatisticalTestModel.id == test_id)
                        .values(**values))
        session.commit()


def _add(db, **values) -> None:
    with db.worker_session() as session:
        session.execute(insert(StatisticalTestModel).values(**values))
        session.commit()


def _delete(db, test_id: str) -> None:
    with db.worker_session() as session:
        session.execute(delete(StatisticalTestModel).where(StatisticalTestModel.id == test_id))
        session.commit()


def _items(db) -> list[dict]:
    return db.client.get("/statistics/tests").json()["items"]


# --- the seed ---


def test_the_seed_is_one_test_on_each_engine(db):
    items = _items(db)

    assert [item["id"] for item in items] == ENGINES
    assert [item["engine"] for item in items] == ENGINES
    assert [item["kind"] for item in items] == ["batch"] * 6 + ["comparison"] * 5
    t_test = items[1]
    assert t_test["engine_settings"] == {"floor": 10, "recommended_times": 30}
    assert t_test["recommended_times"] == 30
    assert items[0]["engine_settings"] == {}
    assert items[0]["floor"]["kind"] == "exact"
    assert items[0]["floor"]["formula"] == "times ≥ ln(1 − confidence) / ln(target)"


# --- rows are the catalogue ---


def test_a_renamed_row_shows_its_new_texts_and_is_named_so_in_errors(db):
    _edit(db, "binomial_gate", name="Pass-rate gate", question="Does it pass often enough?")

    item = _items(db)[0]
    response = db.client.post("/statistics/estimate", json={
        "test_id": db.test()["id"], "statistical_test": "binomial_gate", "times": 5})

    assert (item["name"], item["question"]) == ("Pass-rate gate", "Does it pass often enough?")
    assert response.status_code == 422
    assert "Pass-rate gate" in response.json()["detail"][0]["msg"]


def test_a_new_row_on_an_existing_engine_is_a_new_test(db):
    gate = _row(db, "binomial_gate")
    _add(db, id="strict_gate", name="Strict gate", engine="binomial_gate",
         question="Does each check pass at least 95% of the time, 99% sure?",
         parameters=[{**p, "default": 0.95 if p["key"] == "target" else 0.99}
                     for p in gate.parameters],
         engine_settings={}, floor_explanation=gate.floor_explanation, floor_examples=[],
         method=gate.method)
    test = db.test()

    ids = [item["id"] for item in _items(db)]
    estimate = db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": "strict_gate"}).json()
    batch = db.client.post("/statistics/batches", json={
        "test_id": test["id"], "statistical_test": "strict_gate"}).json()
    db.execute([run.id for run in db.runs_of(batch["id"])])
    finished = db.client.get(f"/statistics/batches/{batch['id']}").json()

    # batch tests first, the new row last among them
    assert ids == ENGINES[:6] + ["strict_gate"] + ENGINES[6:]
    assert (estimate["statistical_test"], estimate["engine"]) == ("strict_gate", "binomial_gate")
    assert estimate["parameters"] == {"target": 0.95, "confidence": 0.99}
    assert estimate["floor"] == 90  # ln(0.01) / ln(0.95) = 89.8, rounded up
    assert (batch["statistical_test"], batch["engine"], batch["floor"]) == (
        "strict_gate", "binomial_gate", 90)
    assert len(db.runs_of(batch["id"])) == 90
    assert (finished["status"], finished["engine"]) == ("Passed", "binomial_gate")


def test_the_t_tests_floor_and_comfortable_size_come_from_its_row(db):
    _edit(db, "one_sample_t", engine_settings={"floor": 20, "recommended_times": 40})
    # no recorded answer and no history: the sizes come from the row alone
    test = db.test(model_output=None, checks=[{"name": "ROUGE", "config": {"threshold": "0.5"}}])

    estimate = db.client.post("/statistics/estimate", json={
        "test_id": test["id"], "statistical_test": "one_sample_t"}).json()

    assert estimate["floor"] == 20
    assert [s["times"] for s in estimate["suggestions"]] == [20, 27, 40]


# --- what a request may name ---


def test_an_unknown_test_is_a_422_on_every_endpoint(db):
    test = db.test()
    a = db.client.post("/statistics/batches", json={
        "test_id": test["id"], "statistical_test": "binomial_gate"}).json()
    b = db.client.post("/statistics/batches", json={
        "test_id": test["id"], "statistical_test": "binomial_gate"}).json()

    responses = [
        db.client.post("/statistics/estimate", json={
            "test_id": test["id"], "statistical_test": "coin_flip"}),
        db.client.post("/statistics/batches", json={
            "test_id": test["id"], "statistical_test": "coin_flip"}),
        db.client.post("/statistics/comparisons", json={
            "batch_a": a["id"], "batch_b": b["id"], "statistical_test": "coin_flip"}),
    ]

    for response in responses:
        assert response.status_code == 422
        [problem] = response.json()["detail"]
        assert problem["loc"] == ["body", "statistical_test"]
        assert problem["msg"] == "Unknown statistical test 'coin_flip'; see GET /statistics/tests"


def test_a_deleted_row_refuses_new_batches_but_its_batches_finish_and_compare(db):
    test = db.test()
    a = db.client.post("/statistics/batches", json={
        "test_id": test["id"], "statistical_test": "binomial_gate"}).json()
    b = db.client.post("/statistics/batches", json={
        "test_id": test["id"], "statistical_test": "binomial_gate"}).json()
    _delete(db, "binomial_gate")

    db.execute([run.id for batch in (a, b) for run in db.runs_of(batch["id"])])
    finished = db.client.get(f"/statistics/batches/{a['id']}").json()
    compared = db.client.post("/statistics/comparisons", json={
        "batch_a": a["id"], "batch_b": b["id"]})
    refused = db.client.post("/statistics/batches", json={
        "test_id": test["id"], "statistical_test": "binomial_gate"})

    assert (finished["status"], finished["statistical_test"], finished["engine"]) == (
        "Passed", "binomial_gate", "binomial_gate")
    assert compared.status_code == 201
    assert compared.json()["batch_a"]["statistical_test"] == "binomial_gate"
    assert refused.status_code == 422
    assert [item["id"] for item in _items(db)] == ENGINES[1:]


# --- a row the code can't run ---


def _gate_row(**overrides) -> StatisticalTestModel:
    fields = dict(
        id="gate", name="Gate", engine="binomial_gate", question="q",
        parameters=[
            {"key": "target", "label": "Target", "kind": "rate", "default": 0.9, "min": 0.5,
             "max": 0.999, "hint": "h"},
            {"key": "confidence", "label": "Confidence", "kind": "level", "default": 0.95,
             "min": 0.8, "max": 0.999, "hint": "h"},
        ],
        engine_settings={}, floor_explanation="e", floor_examples=[], method="m",
    )
    fields.update(overrides)
    return StatisticalTestModel(**fields)


def test_a_sound_row_pairs_with_its_engine():
    entry = entry_of(_gate_row())

    assert entry.engine.id == "binomial_gate"
    assert [p.key for p in entry.parameters] == ["target", "confidence"]
    assert entry.descriptor().floor.formula == "times ≥ ln(1 − confidence) / ln(target)"


@pytest.mark.parametrize("overrides, problem", [
    ({"engine": "coin_flip"}, "names an engine the code doesn't have: 'coin_flip'"),
    ({"parameters": [{"key": "target", "label": "T", "kind": "rate", "default": 0.9,
                      "min": 0.5, "max": 0.999, "hint": "h"}]},
     "defines the parameters ['target']; its engine 'binomial_gate' takes exactly "
     "['confidence', 'target']"),
    ({"parameters": [{"key": "target", "label": "T", "kind": "share", "default": 0.9,
                      "min": 0.5, "max": 0.999, "hint": "h"},
                     {"key": "confidence", "label": "C", "kind": "level", "default": 0.95,
                      "min": 0.8, "max": 0.999, "hint": "h"}]},
     "parameter 'target' is a rate to its engine, not a share"),
    ({"parameters": [{"key": "target", "label": "T", "kind": "rate", "default": 0.3,
                      "min": 0.5, "max": 0.999, "hint": "h"},
                     {"key": "confidence", "label": "C", "kind": "level", "default": 0.95,
                      "min": 0.8, "max": 0.999, "hint": "h"}]},
     "parameter 'target' defaults to 0.3, outside 0.5–0.999"),
    ({"parameters": [{"key": "target", "kind": "rate"}, {"key": "confidence", "kind": "level"}]},
     "has a malformed parameter"),
    ({"engine_settings": {"floor": 10}},
     "reads the settings []; unknown ['floor'], missing []"),
    ({"engine": "one_sample_t", "engine_settings": {"floor": 10},
      "parameters": [{"key": k, "label": k, "kind": "level" if k == "confidence" else "share",
                      "default": 0.5, "min": 0.1, "max": 0.9, "hint": "h"}
                     for k in ("confidence", "difference", "spread")]},
     "reads the settings ['floor', 'recommended_times']; unknown [], missing "
     "['recommended_times']"),
    ({"engine": "one_sample_t", "engine_settings": {"floor": "ten", "recommended_times": 30},
      "parameters": [{"key": k, "label": k, "kind": "level" if k == "confidence" else "share",
                      "default": 0.5, "min": 0.1, "max": 0.9, "hint": "h"}
                     for k in ("confidence", "difference", "spread")]},
     "setting 'floor' must be a whole number of at least 2, not 'ten'"),
    ({"engine": "one_sample_t", "engine_settings": {"floor": 30, "recommended_times": 10},
      "parameters": [{"key": k, "label": k, "kind": "level" if k == "confidence" else "share",
                      "default": 0.5, "min": 0.1, "max": 0.9, "hint": "h"}
                     for k in ("confidence", "difference", "spread")]},
     "recommended_times (10) is below the floor (30)"),
])
def test_a_row_the_code_cant_run_is_refused_naming_the_row(overrides, problem):
    with pytest.raises(CatalogueError, match=r"^Statistical test 'gate'") as error:
        entry_of(_gate_row(**overrides))

    assert problem in str(error.value)
