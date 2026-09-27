"""Per-assignment dispatch, by engine.

Called once per assigned test type from execute_run's loop, never once per
run: a run routinely mixes engines (Exact Match + Toxicity on one test), so
the split lives here, one call at a time, not as separate tasks.

Code implements a handful of generic engines; a catalogue row says which one
scores its type and with which settings. Category stays on the row for
grouping in the UI only — it no longer decides which code runs. Adding a
type that an existing engine can score is a new row, not a new entry here.
"""
from collections.abc import Callable

from assay.models import TestTypesModel
from assay.schemas import EvaluationInput, TestTypeAssignment, TestTypeResult
from assay.worker.evaluators.engines import (
    bertscore,
    bleu,
    contains,
    embedding_cosine,
    exact_match,
    llm_judge,
    meteor,
    regex,
    rouge,
)

Engine = Callable[[EvaluationInput], TestTypeResult]

# Keyed by TestTypesModel.engine. Registering an engine is adding a line here.
ENGINES: dict[str, Engine] = {
    "exact_match": exact_match.evaluate,
    "contains": contains.evaluate,
    "regex": regex.evaluate,
    "rouge": rouge.evaluate,
    "bleu": bleu.evaluate,
    "meteor": meteor.evaluate,
    "bertscore": bertscore.evaluate,
    "embedding_cosine": embedding_cosine.evaluate,
    "llm_judge": llm_judge.evaluate,
}


class UnknownEngineError(LookupError):
    """The catalogue names an engine this worker doesn't have."""


def evaluate(
        assignment: TestTypeAssignment,
        catalogue_row: TestTypesModel | None,
        entry,
        answer: str,
) -> TestTypeResult:
    """Score one assigned test type against the run's frozen copy of the test.

    Both "type not in the catalogue" and "engine not installed" raise rather
    than return a failed result: execute_run already turns any exception into
    that type's passed=False with the message as detail, and raising means
    the same WARNING with a traceback gets logged as for any other evaluator
    failure — a misconfigured catalogue should be visible in the logs, not
    only in one run's results.

    The result is stamped with the engine and settings it was scored with,
    so a later catalogue change can't silently reinterpret it.

    Args:
        assignment: The assigned type (name + per-assignment config).
        catalogue_row: That type's TestTypesModel row, or None if the name
            isn't in the catalogue.
        entry: The frozen copy being evaluated — a StandaloneRunModel or a
            TestSetEntryModel; both expose input/expected_output.
        answer: The text to score — resolved by execute_run (the copy's
            recorded model_output, or the application's reply), not read
            from the entry here, so every engine scores the same answer the
            run records as evaluated_output.
    """
    if catalogue_row is None:
        raise LookupError(f"Test type '{assignment.name}' is not in the catalogue")
    engine = ENGINES.get(catalogue_row.engine)
    if engine is None:
        raise UnknownEngineError(
            f"No engine named '{catalogue_row.engine}' is installed on this worker"
        )

    evaluation = EvaluationInput(
        input=entry.input,
        reference=entry.expected_output,
        answer=answer,
        config=assignment.config or {},
        engine_settings=catalogue_row.engine_settings,
        comparison=catalogue_row.comparison,
    )
    result = engine(evaluation)
    return result.model_copy(update={
        "engine": catalogue_row.engine,
        "engine_settings": catalogue_row.engine_settings,
    })
