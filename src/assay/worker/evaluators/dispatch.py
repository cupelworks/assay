"""Per-assignment dispatch, by test type category — see
docs/run_execution/dev_notes.md note 5. Called once per assigned test
type from tasks/execute_run.py's loop, never once per run: a run
routinely has assignments spanning more than one category (e.g. Exact
Match + Toxicity on the same test), so the category split lives here,
one call at a time, not as separate tasks per category.

`assignment` is a schemas.TestTypeAssignment (name + config) — the same
schema the API already uses, reused rather than inventing a worker-only
shape. `category` is passed alongside it rather than being a field on it:
it's only needed to pick which function to call here — once inside
deterministic.evaluate() (say), the function already knows its own
category. `entry` is whichever ORM object tasks/execute_run.py resolved
the run's content from (TestModel for a standalone run, TestSetEntryModel
for everything else) — both expose the same input/expected_output/
model_output fields, so the evaluators don't need to care which one they
got.
"""
from assay.worker.evaluators import deterministic, llm_as_judge, nlp_metric


def evaluate(assignment, category, entry):
    match category:
        case "deterministic":
            return deterministic.evaluate(assignment, entry)
        case "nlp_metric":
            return nlp_metric.evaluate(assignment, entry)
        case "llm_as_judge":
            return llm_as_judge.evaluate(assignment, entry)
        case _:
            raise ValueError(f"Unknown test type category: {category!r}")
