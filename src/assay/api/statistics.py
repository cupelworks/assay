"""Run with statistics (docs/statistics/): the catalogue of statistical tests,
the estimate shown before a batch is created, batches, and comparisons."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas.statistics import Estimate, EstimateRequest, StatisticalTestCatalogue
from assay.services.statistics import catalogue, estimate_batch

router = APIRouter(tags=["statistics"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_SET_ID = "4e86003a-9e28-4c93-a08e-f99c6acbaab6"
_SCOPE_SET = {"kind": "test_set", "id": _SET_ID, "name": "Support answers"}


def _validation_error(*items: tuple[list, str]) -> dict:
    return {"detail": [{"type": "value_error", "loc": loc, "msg": msg} for loc, msg in items]}


_SCOPE_404 = {
    "description": "The test, test set or test plan doesn't exist. Nothing is created.",
    "content": {"application/json": {"examples": {
        "test_set": {"summary": "Unknown test set",
                     "value": {"detail": "Test set with ID '<test_set_id>' not found"}},
        "test_plan": {"summary": "Unknown test plan",
                      "value": {"detail": "Test plan with ID '<test_plan_id>' not found"}},
    }}},
}
_SCOPE_409 = {
    "description": (
        "The scope can't be run at all, by the same guards an ordinary run applies: an empty "
        "test set, a plan with no linked sets or with an empty one, or an entry (or the "
        "test) with no checks assigned. Nothing is created."
    ),
    "content": {"application/json": {"examples": {
        "empty_set": {"summary": "An empty test set", "value": {
            "detail": "No Test Set Entries found in Test set with ID '<test_set_id>'"}},
        "no_checks": {"summary": "An entry with no checks", "value": {
            "detail": "No test types assigned to Test Set Entries with ids ['<entry_id>']"}},
    }}},
}

_ESTIMATE_GATE = {
    "scope": _SCOPE_SET,
    "statistical_test": "binomial_gate",
    "parameters": {"target": 0.9, "confidence": 0.95},
    "floor": 29,
    "floor_explanation": "At 29 times only a perfect record proves \"at least 90.0%\" with "
                         "95% confidence: 0.9^29 = 0.0471 is at most 0.05. With fewer, no "
                         "result could prove it.",
    "suggestions": [
        {"times": 29, "kind": "floor", "label": "29 · no miss allowed", "default": True},
        {"times": 30, "kind": "absorbs_not_ran", "label": "30 · absorbs one Not Ran",
         "default": False},
        {"times": 46, "kind": "allows_one_miss", "label": "46 · allows one miss",
         "default": False},
    ],
    "times": 29,
    "rule": {"times": 29, "pass_at_least": 29, "fail_at_most": 22},
    "runs_per_time": 2,
    "runs_total": 58,
    "calls": {"application": {"per_time": 2, "total": 58},
              "judge": {"per_time": 1, "total": 29}},
    "checks_total": 3,
    "checks_applicable": 3,
    "entries": [
        {"entry_id": "d4e5f6a7-b8c9-0123-def4-56789012345a",
         "test_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "test_set_id": _SET_ID,
         "test_set_name": "Support answers", "name": "Reset a password",
         "recorded_answer": False,
         "checks": [
             {"label": "Contains", "test_type": "Contains", "applies": True, "reason": None},
             {"label": "Relevance", "test_type": "Relevance", "applies": True,
              "reason": None},
         ]},
        {"entry_id": "e5f6a7b8-c9d0-1234-ef56-7890abcdef12",
         "test_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901", "test_set_id": _SET_ID,
         "test_set_name": "Support answers", "name": "Opening hours",
         "recorded_answer": False,
         "checks": [{"label": "Contains", "test_type": "Contains", "applies": True,
                     "reason": None}]},
    ],
    "warnings": [],
}

_ESTIMATE_T = {
    **_ESTIMATE_GATE,
    "scope": {"kind": "test", "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
              "name": "Summarise the outage report"},
    "statistical_test": "one_sample_t",
    "parameters": {"confidence": 0.95, "difference": 0.05, "spread": 0.1},
    "floor": 10,
    "floor_explanation": "Below 10 scores the spread is too poorly known for a t-test; 27 "
                         "times would see a mean 0.05 of the range from the threshold 80% of "
                         "the time, with scores spreading 0.1 of the range.",
    "suggestions": [
        {"times": 10, "kind": "floor", "label": "10 · the least that means anything",
         "default": False},
        {"times": 27, "kind": "detects_difference", "label": "27 · sees a gap of 0.05",
         "default": True},
        {"times": 30, "kind": "recommended", "label": "30 · comfortable", "default": False},
    ],
    "times": 27,
    "rule": None,
    "runs_per_time": 1,
    "runs_total": 27,
    "calls": {"application": {"per_time": 0, "total": 0},
              "judge": {"per_time": 0, "total": 0}},
    "checks_total": 2,
    "checks_applicable": 1,
    "entries": [{
        "entry_id": None, "test_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "test_set_id": None, "test_set_name": None, "name": "Summarise the outage report",
        "recorded_answer": True,
        "checks": [
            {"label": "ROUGE", "test_type": "ROUGE", "applies": True, "reason": None},
            {"label": "Word Count Limit", "test_type": "Word Count Limit", "applies": False,
             "reason": "Pass/fail only: a t-test needs a score on a scale"},
        ],
    }],
    "warnings": [
        {"code": "recorded_answers",
         "message": "1 of 1 entry has a recorded answer: only their judge checks can vary "
                    "between runs."},
        {"code": "nothing_can_vary",
         "message": "1 recorded entry has no judge check: every run of it gives the same "
                    "result, so its statistics only repeat one run's outcome."},
        {"code": "checks_not_applicable",
         "message": "1 check is not covered by this statistical test: its pass rate is "
                    "shown without a verdict."},
    ],
}


@router.get(
    path="/statistics/tests",
    summary="List the statistical tests",
    responses={200: {
        "description": "Every statistical test Assay offers, with what it asks, the "
                       "parameters it takes, its floor and where the floor comes from.",
    }},
)
async def list_statistical_tests() -> StatisticalTestCatalogue:  # pragma: no cover
    """The catalogue behind **Run with statistics** — the third way to run a test, a
    test set or a test plan, beside running once and replaying.

    Running with statistics runs the same scope **N times** as one *batch*, then answers
    a question about it with a stated confidence — "95% confident each check passes at
    least 90% of the time" — instead of one run's pass or fail. Every time is a full,
    paid run: application calls for answers that aren't recorded, judge calls for LLM
    checks. So it's always a deliberate choice, and the estimate (`POST
    /statistics/estimate`) shows the cost before anything is created.

    Two kinds of test:
    - **`batch`** — run as a batch (`POST /statistics/batches`): the **binomial gate**
      (does each check pass at least a target share of the time?) and the **one-sample
      t-test** (is each scored check's average on the passing side of its threshold?).
    - **`comparison`** — reads two finished batches of the same scope (`POST
      /statistics/comparisons`): **pass rates, A against B** (did my change help?).

    **The floor**, and why there is one: some questions can't be answered below a certain
    size, whatever happens. If a check really passed exactly 90% of the time, 29 passes in
    a row would happen only 4.7% of the time — rarer than 1 in 20 — so 29 straight passes
    prove "above 90%" with 95% confidence; 28 straight passes (5.2%) can't. That 29 is
    *exact* arithmetic. Other floors are *rules of thumb* (the t-test's 10: below it the
    spread is too poorly known) or *none* (Fisher's exact test works at any size, though
    small batches only see big differences). `floor.kind` says which.

    Verdicts are three-way: `pass`, `fail`, or `inconclusive` — not enough runs to say
    either way, which is a different thing from failing. Comparisons answer `better`,
    `worse` or `no_difference`.

    `max_times` and `max_runs` bound one batch (times, and times × entries).
    """
    return catalogue()


@router.post(
    path="/statistics/estimate",
    summary="Estimate a batch before running it",
    responses={
        200: {
            "description": (
                "What a batch of this scope and test would need and cost. **Nothing is "
                "created.** `floor` is the least that can conclude, `suggestions` the sizes "
                "worth offering (one marked `default`), `rule` what the binomial gate "
                "decides at `times`, `calls` what the batch pays for, `entries` which "
                "checks get a verdict, and `warnings` what to know before confirming."
            ),
            "content": {"application/json": {"examples": {
                "gate": {"summary": "A binomial gate on a test set", "value": _ESTIMATE_GATE},
                "t_test": {"summary": "A t-test on a test with a recorded answer",
                           "value": _ESTIMATE_T},
            }}},
        },
        404: _SCOPE_404,
        409: _SCOPE_409,
        422: {
            "description": (
                "The request can't be estimated. FastAPI's own list shape, every problem at "
                "once, each `loc` pointing at the field: not exactly one of `test_id` / "
                "`test_set_id` / `test_plan_id`; a comparison test (`pass_rates`) instead of a "
                "batch test; an unknown parameter or one out of range; `times` below the "
                "floor, above `max_times`, or creating more than `max_runs` runs; parameters "
                "whose floor is above `max_times`; or a t-test on a scope with no scored "
                "check."
            ),
            "content": {"application/json": {"examples": {
                "below_floor": {"summary": "Fewer times than the floor", "value":
                    _validation_error((["body", "times"],
                                       "At least 29 times for the Binomial gate with these "
                                       "parameters: below that no result could conclude "
                                       "anything"))},
                "parameter": {"summary": "A parameter out of range", "value":
                    _validation_error((["body", "parameters", "target"],
                                       "Must be between 0.5 and 0.999"))},
                "floor_too_high": {"summary": "A target too high for one batch", "value":
                    _validation_error((["body", "parameters"],
                                       "These parameters need at least 2995 times, more "
                                       "than the 1000 a batch can run: lower the target or "
                                       "the confidence"))},
                "no_scored_check": {"summary": "A t-test with nothing scored", "value":
                    _validation_error((["body", "statistical_test"],
                                       "None of this scope's checks is scored on a scale: a "
                                       "t-test needs ROUGE, BLEU, METEOR, BERTScore or "
                                       "Cosine Similarity"))},
                "scope": {"summary": "Not exactly one scope", "value":
                    _validation_error((["body"], "Value error, Give exactly one of test_id, "
                                                 "test_set_id or test_plan_id"))},
            }}},
        },
    },
)
async def estimate(request: EstimateRequest, session: SessionDep) -> Estimate:  # pragma: no cover
    """What a batch would need and cost — show this **before** the user confirms
    **Run with statistics**. Nothing is created and nothing is called.

    Send the scope (exactly one of `test_id`, `test_set_id`, `test_plan_id`), a batch
    test from `GET /statistics/tests`, its `parameters` (any left out take their
    default), and optionally `times`. The answer:

    - **`floor`** — the fewest times the test can conclude anything at, with
      `floor_explanation` in plain words for these parameters.
    - **`suggestions`** — sizes worth offering. For the binomial gate: the floor (no miss
      allowed), one more (absorbs one run that can't be evaluated, e.g. an application
      timeout, without costing the verdict), and the size that allows one miss. For the
      t-test: its floor, the size that sees your `difference` 80% of the time, and 30.
      The one marked `default` is used when `times` is left out.
    - **`rule`** — for the binomial gate, what decides at `times`: at least
      `pass_at_least` passes (per check) pass, at most `fail_at_most` fail, anything
      between is inconclusive. Computed here so the UI never recomputes the binomial.
    - **`calls`** — what it pays for, per time and in total: one application call per
      run of an entry with no recorded answer, one judge call per LLM-judge check per
      run. Retries aren't counted.
    - **`entries`** — every entry the batch would run (one for a standalone test), each
      check with whether the test applies to it (a t-test only applies to checks scored
      on a scale; the others still show their pass rate, without a verdict).
    - **`warnings`** — e.g. recorded answers (only judge checks can vary, so a batch over
      them mostly repeats one run), no judge or application configured.

    A test set or plan is resolved as it is **now**: entries added later aren't in a
    batch created from this estimate's numbers unless you estimate again.
    """
    return await estimate_batch(request, session)
