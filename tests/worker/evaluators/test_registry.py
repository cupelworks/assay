from unittest.mock import patch

import pytest

from assay.models import Comparison, StandaloneRunModel, TestSetEntryModel, TestTypesModel
from assay.schemas import EvaluationInput, TestTypeAssignment, TestTypeResult
from assay.worker.evaluators import ENGINES, UnknownEngineError, evaluate

ROUGE = TestTypesModel(
    name="ROUGE", engine="rouge",
    engine_settings={"variant": "rougeL", "measure": "f1", "stemmer": True},
    comparison=Comparison.gte,
)
ENTRY = TestSetEntryModel(input="Summarise the article", expected_output="A short summary")


def test_every_seeded_engine_name_is_registered():
    # every engine a catalogue row names — a row naming anything else fails
    # at run time, per type, with UnknownEngineError below
    assert set(ENGINES) == {
        "exact_match", "contains", "regex", "json", "length", "rouge", "bleu", "meteor",
        "bertscore", "embedding_cosine", "llm_judge",
    }
    assert all(callable(engine) for engine in ENGINES.values())


def test_routes_to_the_rows_engine_with_a_pure_evaluation_input():
    assignment = TestTypeAssignment(name="ROUGE", config={"threshold": "0.7"})
    seen = []

    def fake_engine(evaluation):
        seen.append(evaluation)
        return TestTypeResult(passed=True, score=0.81, detail=None)

    with patch.dict(ENGINES, {"rouge": fake_engine}):
        result = evaluate(assignment, ROUGE, ENTRY, "Summary")

    (evaluation,) = seen
    assert evaluation == EvaluationInput(
        input="Summarise the article",
        reference="A short summary",
        answer="Summary",
        config={"threshold": "0.7"},
        engine_settings={"variant": "rougeL", "measure": "f1", "stemmer": True},
        comparison=Comparison.gte,
    )
    assert (result.passed, result.score) == (True, 0.81)


def test_stamps_the_engine_and_its_settings_on_the_result():
    assignment = TestTypeAssignment(name="ROUGE", config={"threshold": "0.7"})

    with patch.dict(ENGINES, {"rouge": lambda e: TestTypeResult(passed=True, score=0.81,
                                                                 detail=None)}):
        result = evaluate(assignment, ROUGE, ENTRY, "Summary")

    assert result.engine == "rouge"
    assert result.engine_settings == {"variant": "rougeL", "measure": "f1", "stemmer": True}


def test_a_none_config_becomes_an_empty_dict_for_the_engine():
    seen = []

    def fake_engine(evaluation):
        seen.append(evaluation)
        return TestTypeResult(passed=True, score=None, detail=None)

    with patch.dict(ENGINES, {"rouge": fake_engine}):
        evaluate(TestTypeAssignment(name="ROUGE", config=None), ROUGE, ENTRY, "Summary")

    assert seen[0].config == {}


def test_the_answer_handed_in_is_what_the_engine_sees_not_the_entrys_model_output():
    entry = TestSetEntryModel(input="q", expected_output="r", model_output="stale recorded text")
    seen = []

    def fake_engine(evaluation):
        seen.append(evaluation)
        return TestTypeResult(passed=True, score=None, detail=None)

    with patch.dict(ENGINES, {"rouge": fake_engine}):
        evaluate(TestTypeAssignment(name="ROUGE"), ROUGE, entry, "the application's reply")

    assert seen[0].answer == "the application's reply"


def test_a_standalone_copy_and_a_set_entry_look_the_same_to_the_engine():
    copy = StandaloneRunModel(input="Summarise the article", expected_output="A short summary")
    seen = []

    def fake_engine(evaluation):
        seen.append(evaluation)
        return TestTypeResult(passed=True, score=None, detail=None)

    with patch.dict(ENGINES, {"rouge": fake_engine}):
        evaluate(TestTypeAssignment(name="ROUGE"), ROUGE, copy, "Summary")
        evaluate(TestTypeAssignment(name="ROUGE"), ROUGE, ENTRY, "Summary")

    assert seen[0] == seen[1]


def test_an_unknown_engine_is_that_types_failure_naming_the_engine():
    row = TestTypesModel(name="Politeness", engine="vibes", engine_settings={}, comparison=None)

    with pytest.raises(UnknownEngineError, match="No engine named 'vibes' is installed"):
        evaluate(TestTypeAssignment(name="Politeness"), row, ENTRY, "x")


def test_a_type_missing_from_the_catalogue_is_that_types_failure():
    with pytest.raises(LookupError, match="Test type 'Retired' is not in the catalogue"):
        evaluate(TestTypeAssignment(name="Retired"), None, ENTRY, "x")


def test_the_remaining_stubs_keep_their_fixed_outcomes():
    # The deterministic engines score for real; the metric and judge engines
    # still return one fixed outcome each, until they're replaced one at a
    # time.
    evaluation = EvaluationInput(input="q", reference="a", answer="a")

    for name in ("rouge", "bleu", "meteor", "bertscore", "embedding_cosine"):
        assert ENGINES[name](evaluation) == TestTypeResult(passed=True, score=1.0, detail=None)
    assert ENGINES["llm_judge"](evaluation) == TestTypeResult(passed=True, score=None,
                                                               detail="Testing")


def test_the_deterministic_engines_score_for_real_through_the_registry():
    row = TestTypesModel(name="Exact Match", engine="exact_match",
                         engine_settings={"trim": True, "case_sensitive": True}, comparison=None)
    entry = TestSetEntryModel(input="q", expected_output="hi")

    result = evaluate(TestTypeAssignment(name="Exact Match"), row, entry, "hi\n")

    assert (result.passed, result.score, result.engine) == (True, None, "exact_match")
