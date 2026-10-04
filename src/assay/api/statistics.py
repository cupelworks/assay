"""Run with statistics (docs/version_1/statistics/): the catalogue of statistical tests,
the estimate shown before a batch is created, batches, and comparisons."""
import inspect
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from assay.api import _statistics_examples as computed
from assay.api._estimate_example import WITH_HISTORY as _ESTIMATE_WITH_HISTORY
from assay.db import get_session
from assay.schemas.statistics import (
    BatchDetails,
    BatchList,
    BatchRequest,
    BatchStatusName,
    ComparisonDetails,
    ComparisonList,
    ComparisonRequest,
    Estimate,
    EstimateRequest,
    StatisticalEngine,
    StatisticalTestCatalogue,
)
from assay.services.statistics import (
    catalogue,
    create_batch,
    create_comparison,
    estimate_batch,
    get_batch,
    get_comparison,
    list_batches,
    list_comparisons,
    load_catalogue,
    stop_batch,
)
from assay.services.statistics.compare import all_verdicts, outcome

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

# what an estimate says about a check and the batch when nothing has run yet
_NO_HISTORY = {"left_out": False, "history": None, "outlook": "unknown",
               "outlook_reason": "No runs yet: nothing to plan from.", "certain_result": None,
               "size_needed": None, "best_chance": None, "cheaper": []}
_NO_ODDS = {"goal": 0.9, "goal_reachable": None, "best_chance": None, "best_times": None,
            "odds_summary": None, "driving_check": None, "odds": [], "cheaper": [],
            "until_answer": None}
_SIZE_ONLY = {"chance": None, "failures_allowed": None, "outcome": None,
              "likely_undecided": None}


_ESTIMATE_GATE = {
    "scope": _SCOPE_SET,
    "statistical_test": "binomial_gate",
    "engine": "binomial_gate",
    "parameters": {"target": 0.9, "confidence": 0.95},
    "floor": 29,
    "floor_explanation": "A few in a row can be luck. A check that really passed only 9 times "
                         "in 10 would pass 29 times in a row less than 1 time in 20, so 29 "
                         "out of 29 is enough to be 95% sure. With fewer, even a perfect "
                         "record could be luck.",
    "suggestions": [
        {"times": 29, "kind": "floor", "label": "29 times · no failure allowed",
         "default": True, **_SIZE_ONLY},
        {"times": 30, "kind": "absorbs_not_ran", **_SIZE_ONLY,
         "label": "30 times · one spare, in case a run can't run",
         "default": False},
        {"times": 46, "kind": "allows_one_miss", "label": "46 times · one failure allowed",
         **_SIZE_ONLY,
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
    # entries in the one order statistics use: set name, then entry name
    "entries": [
        {"entry_id": "e5f6a7b8-c9d0-1234-ef56-7890abcdef12",
         "test_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901", "test_set_id": _SET_ID,
         "test_set_name": "Support answers", "name": "Opening hours",
         "recorded_answer": False,
         "checks": [{"label": "Contains", "test_type": "Contains", "applies": True,
                     "reason": None, "target": 0.9, **_NO_HISTORY}]},
        {"entry_id": "d4e5f6a7-b8c9-0123-def4-56789012345a",
         "test_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "test_set_id": _SET_ID,
         "test_set_name": "Support answers", "name": "Reset a password",
         "recorded_answer": False,
         "checks": [
             {"label": "Contains", "test_type": "Contains", "applies": True, "reason": None,
              "target": 0.9, **_NO_HISTORY},
             {"label": "Relevance", "test_type": "Relevance", "applies": True,
              "reason": None, "target": 0.9, **_NO_HISTORY},
         ]},
    ],
    "warnings": [],
    **_NO_ODDS,
    # nothing has run yet: learn first
    "trial": {"statistical_test": "trial", "times": 10, "application_calls": 20,
              "judge_calls": 10,
              "reason": "3 checks have never run: a trial of 10 times shows how they behave, "
                        "so the batch can be planned from it."},
}

_ESTIMATE_T = {
    **_ESTIMATE_GATE,
    "scope": {"kind": "test", "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
              "name": "Summarise the outage report"},
    "statistical_test": "one_sample_t",
    "engine": "one_sample_t",
    "parameters": {"confidence": 0.95, "difference": 0.05, "spread": 0.1},
    "floor": 10,
    "floor_explanation": "An average of fewer than 10 scores is too unreliable to judge. To "
                         "spot an average 5% of the score range away from the threshold, when "
                         "scores usually vary by about 10% of the range, takes about 27 times.",
    # a recorded answer gives the same score every run: certain, so its floor
    # answers it, and nothing more is worth paying for
    "suggestions": [
        {"times": 10, "kind": "reaches_goal", "label": "10 times · 100% chance of an answer",
         "default": True, "chance": 1.0, "failures_allowed": None, "outcome": None,
         "likely_undecided": None},
    ],
    "times": 10,
    "rule": None,
    "runs_per_time": 1,
    "runs_total": 10,
    "calls": {"application": {"per_time": 0, "total": 0},
              "judge": {"per_time": 0, "total": 0}},
    "checks_total": 2,
    "checks_applicable": 1,
    "entries": [{
        "entry_id": None, "test_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "test_set_id": None, "test_set_name": None, "name": "Summarise the outage report",
        "recorded_answer": True,
        "checks": [
            {"label": "ROUGE", "test_type": "ROUGE", "applies": True, "reason": None,
             "target": None, "left_out": False, "history": None, "outlook": "certain",
             "outlook_reason": "It can't vary — a recorded answer read by a fixed check — "
                               "so one run tells its result.",
             "certain_result": None, "size_needed": 10, "best_chance": 1.0, "cheaper": []},
            {"label": "Word Count Limit", "test_type": "Word Count Limit", "applies": False,
             "reason": "It only passes or fails: an average needs a check that gives a "
                       "score", "target": None, "left_out": False, "history": None,
             "outlook": None, "outlook_reason": None, "certain_result": None,
             "size_needed": None, "best_chance": None, "cheaper": []},
        ],
    }],
    "warnings": [
        {"code": "recorded_answers",
         "message": "The test has a recorded answer: only its judge checks can vary between "
                    "runs."},
        {"code": "nothing_can_vary",
         "message": "1 recorded entry has no judge check: every run of it gives the same "
                    "result, so running it many times only repeats that one result."},
        {"code": "checks_not_applicable",
         "message": ("1 check isn't something this test can judge: you'll see how often it passed, "
                    "without an answer.")},
    ],
    "goal": 0.9, "goal_reachable": True, "best_chance": 1.0, "best_times": 10,
    "odds_summary": "10 times give every check an answer 100% of the time.",
    "driving_check": None,
    "odds": [{"times": n, "chance": 1.0} for n in (10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125,
                                                  150, 200, 250, 300, 400, 500, 600, 750,
                                                  1000)],
    "cheaper": [], "trial": None,
    # running until there's an answer instead: certain too, so its first wave answers it
    "until_answer": {
        "statistical_test": "sequential_t", "max_times": 100, "first_wave": 10,
        "wave_size": 13, "looks": [10, 23, 36, 49, 61, 74, 87, 100], "usual_times": 10,
        "expected_times": 10, "chance_by_max": 1.0, "outcome": None, "likely_undecided": None,
        "summary": "Up to 100 times, it usually stops by 10 times (about 10 on average), and "
                   "every check has its answer 100% of the time."},
}


@router.get(
    path="/statistics/tests",
    summary="List the statistical tests",
    responses={200: {
        "description": "Every statistical test Assay offers — one per `statistical_tests` "
                       "row — with the engine it runs, what it asks, the parameters it "
                       "takes, its floor and where the floor comes from.",
    }},
)
async def list_statistical_tests(
        session: SessionDep) -> StatisticalTestCatalogue:  # pragma: no cover
    """The catalogue behind **Run with statistics** — the third way to run a test, a
    test set or a test plan, beside running once and replaying.

    Running with statistics runs the same scope **N times** as one *batch*, then answers
    a question about it with a stated confidence — "95% confident each check passes at
    least 90% of the time" — instead of one run's pass or fail. Every time is a full,
    paid run: application calls for answers that aren't recorded, judge calls for LLM
    checks. So it's always a deliberate choice, and the estimate (`POST
    /statistics/estimate`) shows the cost before anything is created.

    Two kinds of test (seeded rows; the names are the catalogue's own, in plain words):
    - **`batch`** — run as a batch (`POST /statistics/batches`):
      - **Passes reliably** (`binomial_gate`) — does each check pass at least a target share
        of the time?
      - **Scores high enough on average** (`one_sample_t`) — is each scored check's average
        on the passing side of its threshold?
      - **The judge is consistent** (`judge_stability`) — does each LLM judge give the same
        verdict on the same recorded answer? (judge checks of recorded answers only)
      - **Learn how it behaves** (`trial`) — a few runs with no verdict, so the next batch
        can be planned from them; a trial ends `Done`.
      - **…until there's an answer** (`sequential_gate`, `sequential_t`,
        `sequential_judge_stability`) — the three questions run in waves up to a maximum,
        stopping as soon as every check has its answer; `times` is the maximum.
    - **`comparison`** — reads two finished batches of the same scope (`POST
      /statistics/comparisons`), A the baseline and B the change:
      - **pass rates, A against B** — did a check pass more or less often?
      - **no worse than A** — is B at most a margin worse? The release-gate question: a
        difference test can't prove "no difference", this proves "at most 5 points worse".
      - **mean scores** (Welch) and **score ranks** (Mann–Whitney) — did a scored check's
        scores move? Ranks when scores bunch against 0 or 1.
      - **paired by entry** — on the same entries, did B do better? One verdict over every
        (entry, check) pair: the strongest "did my change help?" for a test set.

    `wave` says which build wave a test came in (1, 2, or 3 for the trial and "until
    there's an answer"); all are available.

    **The catalogue is a table**, like the check types: each row names the `engine` in code
    that does its arithmetic and carries the texts, each parameter's default and range, and
    the engine's settings (`engine_settings`). A new test on an existing engine — a stricter
    gate with other defaults — is a row, not code. `id` is what to send as
    `statistical_test`; a batch records its row's id and engine and is finished with the
    engine, so editing a row later never changes a stored batch.

    **The floor**, and why there is one: some questions can't be answered below a certain
    size, whatever happens. If a check really passed exactly 90% of the time, 29 passes in
    a row would happen only 4.7% of the time — rarer than 1 in 20 — so 29 straight passes
    prove "above 90%" with 95% confidence; 28 straight passes (5.2%) can't. That 29 is
    *exact* arithmetic. Other floors are *rules of thumb* (the t-test's 10: below it the
    spread is too poorly known) or *none* (Fisher's exact test works at any size, though
    small batches only see big differences). `floor.kind` says which.

    Verdicts are three-way: a batch test answers `pass`, `fail`, or `inconclusive` — not
    enough runs to say either way, which is a different thing from failing. The comparisons
    answer `better`, `worse` or `no_difference` (no real difference *at this size*), except
    `no_worse`, which answers `no_worse`, `worse` or `inconclusive`. Each item's `verdicts`
    lists its own.

    `max_times` and `max_runs` bound one batch (times, and times × entries).
    """
    return catalogue(await load_catalogue(session))


@router.post(
    path="/statistics/estimate",
    summary="Estimate a batch before running it",
    responses={
        200: {
            "description": (
                "What a batch of this scope and test would need and cost, planned from what "
                "the checks have already shown. **Nothing is created.** With a history for "
                "every check: each size's `chance` of an answer, the `odds` curve, the "
                "default size for a `goal` chance (or the one worth its cost when no size "
                "reaches it, `odds_summary` saying so), each check's `outlook`, the "
                "`driving_check` and what would make it `cheaper`. Without: the plain sizes "
                "and a `trial` to run first. Always: `floor`, `rule`, `calls`, `entries`, "
                "`warnings`."
            ),
            "content": {"application/json": {"examples": {
                "with_history": {"summary": "Planned from 40 runs of history: the goal "
                                            "is out of reach, the size worth its cost",
                                 "value": _ESTIMATE_WITH_HISTORY},
                "gate": {"summary": "Nothing has run yet: the plain sizes, and a trial first",
                         "value": _ESTIMATE_GATE},
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
                "floor, above `max_times` (50 for a trial), or creating more than `max_runs` "
                "runs; parameters whose floor is above `max_times`; a t-test on a scope with "
                "no scored check; or `targets` / `leave_out` that don't fit the scope (an "
                "unknown check, a target out of range or on a test without one, a target "
                "set twice, an entry with every check left out)."
            ),
            "content": {"application/json": {"examples": {
                "below_floor": {"summary": "Fewer times than the floor", "value":
                    _validation_error((["body", "times"],
                                       "Passes reliably needs at least 29 times with these "
                                       "settings: with fewer, no result could give an "
                                       "answer"))},
                "parameter": {"summary": "A parameter out of range", "value":
                    _validation_error((["body", "parameters", "target"],
                                       "Must be between 0.5 and 0.999"))},
                "floor_too_high": {"summary": "A target too high for one batch", "value":
                    _validation_error((["body", "parameters"],
                                       "These settings need at least 2995 times, more than "
                                       "the 1000 a batch can run: lower the target or how "
                                       "sure you want to be"))},
                "no_scored_check": {"summary": "A t-test with nothing scored", "value":
                    _validation_error((["body", "statistical_test"],
                                       "None of these checks gives a score: an average "
                                       "needs ROUGE, BLEU, METEOR, BERTScore or Cosine "
                                       "Similarity"))},
                "no_judge_check": {"summary": "Judge stability with no judge to test", "value":
                    _validation_error((["body", "statistical_test"],
                                       "None of these checks is an LLM judge on a recorded "
                                       "answer: this test needs one (Correctness, Relevance, "
                                       "Bias, Toxicity or Hallucination, on a test with a "
                                       "recorded answer)"))},
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
    default), and optionally `times`, `targets` and `leave_out`.

    **Planned from history.** Each check's history is its most recent runs (up to 200)
    that asked the same question and gave it a result — a set entry's runs, or a
    standalone test's runs since its last edit; Not Ran runs and errored results don't
    count. When every check has one, the estimate says how likely each size is to give
    every check an answer, averaged over what the checks' true rates could be — so it's
    honest about how little a short history proves:

    - **`suggestions`** — the floor, then the fewest times with a **`goal`** (90%) chance
      that every check gets an answer (`reaches_goal`, the default); when no size within
      one batch reaches it, the fewest within 5 points of the best (`worth_its_cost`, the
      default) and the best (`best_chance`). Each with its `chance`, `failures_allowed`
      (at the batch's target), the `outcome` it would most likely end in (`passed` /
      `inconclusive` / `failed`) and the check most likely to stay undecided.
    - **`goal_reachable`**, **`best_chance`** / **`best_times`**, and **`odds_summary`**,
      the odds in one sentence to show as it is.
    - **`odds`** — the chart: the chance that every check answers, at each size where one
      more failure becomes allowed (the curve's peaks).
    - **`driving_check`** — the check that needs the most runs: where to act.
    - Per check (`entries[].checks[]`): **`history`**, **`outlook`** (`likely_pass`,
      `likely_fail`, `too_close` to call, `unknown`, `certain`) with `outlook_reason`,
      **`size_needed`** and **`best_chance`** alone, and **`cheaper`**: a lower target
      for this check, or leaving it out — each with the size the batch would then default
      to, its chance, and its ceiling (`best_chance`, `best_times`).
    - **`cheaper`** (the batch): one step less sure, and, when no single change reaches
      the goal, the fewest checks to leave out together.

    A recorded answer read by anything but a judge can't vary: `certain`, answered by the
    floor, and nothing is cheaper than running it once.

    **Without a history for every check**, the sizes are the plain ones: for the binomial
    gate the floor (no failure allowed), one more (absorbs a run that can't be
    evaluated), and the size that allows one failure; for the t-test its floor, the size
    that sees your `difference`, and 30. And **`trial`** offers a trial to run first —
    `statistical_test: "trial"`, 10 times, no verdict — whose runs become the history.

    **Per-check choices**: **`targets`** gives one check its own target (`{entry_id,
    label, target}`; `entry_id` null for a standalone test) and **`leave_out`** drops
    checks from the batch (`{entry_id, label}`): their runs skip them, so their judge
    calls aren't made. The estimate reflects both; `floor` follows the targets in play.

    Always:
    - **`floor`** and `floor_explanation`; **`times`** — yours, or the default (capped,
      with a `times_capped` warning, if it would create more runs than a batch may);
    - **`rule`** — for a target test, what decides at `times` (at least `pass_at_least`
      passes pass, at most `fail_at_most` fail);
    - **`calls`** — application and judge calls per time and in total;
    - **`entries`** — every entry and check, and whether the test applies to it;
    - **`warnings`** — recorded answers, nothing configured, checks the test can't judge.

    A test set or plan is resolved as it is **now**: entries added later aren't in a
    batch created from this estimate's numbers unless you estimate again.
    """
    return await estimate_batch(request, session)


# ── batches ──────────────────────────────────────────────────────────────────

_BATCH_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
_ENTRY_ID = "d4e5f6a7-b8c9-0123-def4-56789012345a"
_EXECUTION_IDS = ["1b4e28ba-2fa1-11d2-883f-0016d3cca427", "6fa459ea-ee8a-3ca4-894e-db77e160355e",
                  "886313e1-3b8a-5372-9b90-0c9aee199e5d"]
_RUN_IDS = ["9b2f7c1e-0f4a-4d3b-8a51-2c7e5d9f1a01", "9b2f7c1e-0f4a-4d3b-8a51-2c7e5d9f1a02",
            "9b2f7c1e-0f4a-4d3b-8a51-2c7e5d9f1a03"]


def _counts(**given) -> dict:
    return {key: given.get(key, 0)
            for key in ("Pending", "Running", "Green", "Amber", "Red", "NotRan")}


_BATCH_COMMON = {
    "id": _BATCH_ID,
    "scope": _SCOPE_SET,
    "statistical_test": "binomial_gate",
    "engine": "binomial_gate",
    "parameters": {"target": 0.9, "confidence": 0.95},
    "note": "Prompt v3, temperature 0.2",
    "floor": 29,
    "created_at": "2026-10-01T09:30:00+02:00",
    "stopped_at": None,
}

_ENTRY_B_ID = "e5f6a7b8-c9d0-1234-ef56-7890abcdef13"
_RUN_B_IDS = ["9b2f7c1e-0f4a-4d3b-8a51-2c7e5d9f1b01", "9b2f7c1e-0f4a-4d3b-8a51-2c7e5d9f1b02",
              "9b2f7c1e-0f4a-4d3b-8a51-2c7e5d9f1b03"]


def _live_entry(entry_id: str, name: str, run_ids: list[str], statuses: list[str]) -> dict:
    """A row of the runs matrix while the batch runs (statuses by time, cut to
    three points like the series)."""
    counts: dict[str, int] = {}
    for status_ in statuses:
        counts[status_] = counts.get(status_, 0) + 1
    return {
        "entry_id": entry_id, "test_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "test_set_id": _SET_ID, "test_set_name": "Support answers", "name": name,
        "runs": _counts(**counts),
        "strip": [{"index": i, "run_id": run_ids[i - 1], "execution_id": _EXECUTION_IDS[i - 1],
                   "status": statuses[i - 1]} for i in (1, 2, 3)],
    }


_BATCH_PENDING = {
    **_BATCH_COMMON,
    "status": "Pending",
    "progress": {
        "times_requested": 30, "times_done": 0, "runs_total": 60, "runs_done": 0,
        "runs": _counts(Pending=60), "runs_cancelled": 0, "times_cancelled": 0,
        "calls": {"application": {"planned": 60, "finished": 0, "in_flight": 0},
                  "judge": {"planned": 30, "finished": 0, "in_flight": 0}},
        "entries": [
            _live_entry(_ENTRY_ID, "Reset a password", _RUN_IDS, ["Pending"] * 3),
            _live_entry(_ENTRY_B_ID, "Unknown order number", _RUN_B_IDS, ["Pending"] * 3),
        ],
    },
    "summary": None,
    "verdicts": None,
    "completed_at": None,
    "result": None,
}

_BATCH_RUNNING = {
    **_BATCH_PENDING,
    "status": "Running",
    "progress": {
        "times_requested": 30, "times_done": 12, "runs_total": 60, "runs_done": 25,
        "runs": _counts(Pending=33, Running=2, Green=22, Amber=2, NotRan=1),
        "runs_cancelled": 0, "times_cancelled": 0,
        "calls": {"application": {"planned": 60, "finished": 25, "in_flight": 2},
                  "judge": {"planned": 30, "finished": 12, "in_flight": 1}},
        "entries": [
            _live_entry(_ENTRY_ID, "Reset a password", _RUN_IDS,
                        ["Green", "Amber", "Running"]),
            _live_entry(_ENTRY_B_ID, "Unknown order number", _RUN_B_IDS,
                        ["Green", "Green", "Pending"]),
        ],
    },
}


def _point(index: int, passed: bool | None, status_: str = "Green", score=None,
           error=None) -> dict:
    return {"index": index, "run_id": _RUN_IDS[index - 1],
            "execution_id": _EXECUTION_IDS[index - 1], "status": status_, "passed": passed,
            "score": score, "error": error}


_GATE_PASS_CHECK = {
    "label": "Mentions the reset link", "test_type": "Contains", "applies": True,
    "reason": None, "scale": None, "threshold": None, "comparison": None, "target": 0.9,
    "counts": {"evaluated": 30, "passed": 30, "failed": 0, "errored": 0, "not_ran": 0},
    "pass_rate": {"lower": 0.905, "point": 1.0, "upper": 1.0, "method": "exact",
                  "level": 0.95, "sides": "one"},
    "scores": None,
    "statistic": {
        "verdict": "pass",
        "reason": "Passed 30 of 30 runs: it passes at least 9 times in 10 (95% sure).",
        "n": 30,
        "interval": {"lower": 0.905, "point": 1.0, "upper": 1.0, "method": "exact",
                     "level": 0.95, "sides": "one"},
        "p_value_pass": 0.0424, "p_value_fail": 1.0,
        "rule": {"times": 30, "pass_at_least": 30, "fail_at_most": 23},
        "t": None, "df": None, "standard_error": None,
        "times_to_decide": None, "times_to_decide_message": None,
    },
    "series": [_point(1, True), _point(2, True), _point(3, True)],
}

_GATE_UNDECIDED_CHECK = {
    "label": "Relevance", "test_type": "Relevance", "applies": True, "reason": None,
    "scale": None, "threshold": None, "comparison": None, "target": 0.9,
    "counts": {"evaluated": 30, "passed": 28, "failed": 2, "errored": 0, "not_ran": 0},
    "pass_rate": {"lower": 0.8047, "point": 0.9333, "upper": 0.988, "method": "exact",
                  "level": 0.95, "sides": "one"},
    "scores": None,
    "statistic": {
        "verdict": "inconclusive",
        "reason": ("Can't tell yet: it passed 28 of 30 runs. That's close to 9 times in 10, and 30 "
                   "runs aren't enough to know which side it's on."),
        "n": 30,
        "interval": {"lower": 0.8047, "point": 0.9333, "upper": 0.988, "method": "exact",
                     "level": 0.95, "sides": "one"},
        "p_value_pass": 0.4114, "p_value_fail": 0.8163,
        "rule": {"times": 30, "pass_at_least": 30, "fail_at_most": 23},
        "t": None, "df": None, "standard_error": None,
        "times_to_decide": 215,
        "times_to_decide_message": "A new batch of about 215 times would likely show that it "
                                   "passes at least 9 times in 10.",
    },
    "series": [_point(1, True, "Amber"), _point(2, False, "Amber"), _point(3, True)],
}

_BATCH_FINISHED = {
    **_BATCH_COMMON,
    "status": "Inconclusive",
    "progress": {
        "times_requested": 30, "times_done": 30, "runs_total": 30, "runs_done": 30,
        "runs": _counts(Green=28, Amber=2), "runs_cancelled": 0, "times_cancelled": 0,
        "calls": {"application": {"planned": 30, "finished": 30, "in_flight": 0},
                  "judge": {"planned": 30, "finished": 30, "in_flight": 0}},
        "entries": None,
    },
    "summary": ("Inconclusive: of the 2 checks, 1 met the goal and 1 can't be told yet. A bigger "
                "batch would settle it."),
    "verdicts": {"pass": 1, "fail": 0, "inconclusive": 1, "none": 0},
    "completed_at": "2026-10-01T09:52:41+02:00",
    "result": {
        "computed_at": "2026-10-01T09:52:41+02:00",
        "checks_total": 2,
        "checks_applicable": 2,
        "verdicts": {"pass": 1, "fail": 0, "inconclusive": 1, "none": 0},
        "summary": ("Inconclusive: of the 2 checks, 1 met the goal and 1 can't be told yet. A "
                    "bigger batch would settle it."),
        "entries": [{
            "entry_id": _ENTRY_ID, "test_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "test_set_id": _SET_ID, "test_set_name": "Support answers",
            "name": "Reset a password", "recorded_answer": False,
            "runs": _counts(Green=28, Amber=2),
            "checks": [_GATE_PASS_CHECK, _GATE_UNDECIDED_CHECK],
            "strip": [{"index": i, "run_id": _RUN_IDS[i - 1],
                       "execution_id": _EXECUTION_IDS[i - 1],
                       "status": "Amber" if i == 2 else "Green"} for i in (1, 2, 3)],
        }],
    },
}

_T_CHECK = {
    "label": "ROUGE", "test_type": "ROUGE", "applies": True, "reason": None,
    "scale": {"min": 0.0, "max": 1.0}, "threshold": 0.5, "comparison": "gte", "target": None,
    "counts": {"evaluated": 10, "passed": 10, "failed": 0, "errored": 0, "not_ran": 0},
    "pass_rate": {"lower": 0.7225, "point": 1.0, "upper": 1.0, "method": "wilson",
                  "level": 0.95, "sides": "two"},
    "scores": {"n": 10, "mean": 0.605, "sd": 0.0337, "min": 0.55, "max": 0.66, "p10": 0.568,
               "p25": 0.5825, "median": 0.605, "p75": 0.6275, "p90": 0.642},
    "statistic": {
        "verdict": "pass",
        "reason": "Average score 0.605 over 10 runs: safely above the 0.5 needed (95% sure).",
        "n": 10,
        "interval": {"lower": 0.5854, "point": 0.605, "upper": 0.6246, "method": "t",
                     "level": 0.95, "sides": "one"},
        "p_value_pass": 0.0, "p_value_fail": 1.0, "rule": None,
        "t": 9.8389, "df": 9, "standard_error": 0.0107,
        "times_to_decide": None, "times_to_decide_message": None,
    },
    "series": [
        {"index": 1, "run_id": _RUN_IDS[0], "execution_id": None, "status": "Green",
         "passed": True, "score": 0.61, "error": None},
        {"index": 2, "run_id": _RUN_IDS[1], "execution_id": None, "status": "Green",
         "passed": True, "score": 0.58, "error": None},
    ],
}

_STOPPED_RESULT = computed.stopped_result()
_BATCH_STOPPED = {
    **_BATCH_COMMON,
    "status": "Incomplete",
    "stopped_at": "2026-10-01T09:41:07+02:00",
    "progress": {
        "times_requested": 30, "times_done": 30, "runs_total": 60, "runs_done": 60,
        "runs": _counts(Green=22, Amber=2, NotRan=36), "runs_cancelled": 36,
        "times_cancelled": 18,
        "calls": {"application": {"planned": 60, "finished": 24, "in_flight": 0},
                  "judge": {"planned": 30, "finished": 12, "in_flight": 0}},
        "entries": None,
    },
    "summary": _STOPPED_RESULT["summary"],
    "verdicts": _STOPPED_RESULT["verdicts"],
    "completed_at": "2026-10-01T09:41:09+02:00",
    # a stopped batch keeps what ran: rates, ranges and series, here without a
    # verdict since 12 evaluated runs are below the floor of 29
    "result": _STOPPED_RESULT,
}

_ONE_PROVEN = {"pass": 1, "fail": 0, "inconclusive": 0, "none": 0}

_BATCH_404 = {
    "description": "No batch with this id.",
    "content": {"application/json": {"example": {
        "detail": f"Statistical batch with ID {_BATCH_ID} not found"}}},
}

_BATCH_ANATOMY = """
**Reading a batch** (one shape for every status):

- **`status`** — `Pending` (no run started yet) → `Running` → one outcome: `Passed` (every
  applicable check proven), `Failed` (at least one check proven to fail — one proven failure
  fails the batch), `Inconclusive` (finished, but not every check could be decided at this
  size — a bigger batch would decide the undecided), `Incomplete` (stopped before every run
  ran) or `NotRan` (nothing could be decided: every run Not Ran — e.g. no application
  configured — or every check errored in every run — e.g. no judge chosen; fix that first,
  a bigger batch wouldn't help); a trial ends `Done` (no question, no verdict). `Passed`
  and `Failed` are a batch's words, never a run's: a run stays `Green`/`Amber`/`Red`. A
  batch that runs until there's an answer stays `Running` between its waves, and ends
  `Passed` or `Failed` as soon as every check has its answer — `Inconclusive` only at its
  maximum.
- **`progress`** — `times_done` of `times_requested` (a time is done when all its runs are),
  `runs_done` of `runs_total`, runs by status, and `calls`: application and judge calls
  `planned`, `finished` and `in_flight` — what's been spent, for the Stop decision. While
  the batch runs, this read also carries `progress.entries`: the runs matrix as it fills
  in (one row per entry, each run's `status` by time, with `run_id` to open it) — statuses
  only, no verdicts. It's null in lists, with `series=false`, and once there is a result.
  For a batch that runs until there's an answer, `progress.waves`: waves `released` of
  `planned`, the `looks` (where each wave ends), `closed`, `stopped_early`.
- **`targets`** and **`leave_out`** — the per-check choices it was created with.
- **`result`** — null until every run has finished: there are **no verdicts mid-batch**
  (an interim verdict invites stopping on a lucky streak). The first read that finds every
  run finished computes it and stores it; later reads return exactly the same.
  `summary` is the outcome in a sentence; `verdicts` counts the applicable checks by
  verdict (both repeated at the top level, where lists have them too); then `entries` —
  one per entry of the scope (one for a test), each with:
  - `runs` by status and `strip`, its runs by time (a row of the runs matrix: one row per
    entry, one column per time);
  - `checks`, in label order, each with
    - the chart's **frame**: `scale` (`min`/`max`, the y axis of a scored check),
      `threshold` and `comparison` (`gte`: at or above passes) for a threshold line,
      `target` for the gate's line on a pass-rate chart;
    - **`counts`**: `evaluated` = `passed` + `failed` is the sample; `errored` (the check
      itself failed to run in that run — a judge timeout) and `not_ran` (the whole run was
      Not Ran) are counted apart and say nothing about the check;
    - **`pass_rate`** with its range, and for scored checks **`scores`** (mean, sd, min,
      max, p10, p25, median, p75, p90 — a box plot without recomputing);
    - **`statistic`** — `verdict` (`pass`/`fail`/`inconclusive`, or null when none could
      be drawn), `reason` as a sentence, `interval` (the very bounds the verdict used, so
      a whisker can't contradict it), p-values each way, the gate's `rule` at this size,
      the t-test's `t`/`df`/`standard_error`, and when inconclusive `times_to_decide`: the
      size of a **new** batch that would likely decide it (never an extension of this one).
      Null when the test doesn't apply to the check (`reason` says why);
    - **`series`** — one point per run, by `index` (the time): `passed`, `score`, the run's
      `status`, and `error` when there's no result. A pass/fail strip, a score scatter, a
      histogram, a running pass rate, and a click-through to the run (`run_id`,
      `execution_id`).

Every interval says what it is: `method` (`exact` Clopper–Pearson, `wilson`, `t`), `level`,
and `sides` (`one`: each bound is one-sided at `level` — the kind a verdict "at least X"
is drawn from). Numbers are rounded to four decimals; `null` means the value doesn't exist,
never zero.
"""


_CREATE_STATISTICAL_BATCH_DOC = inspect.cleandoc("""
    Run a test, a test set or a test plan **N times as one batch**, and get a statistical
    answer once every run has finished.

    What it creates, in one transaction:
    - a **test**: N standalone runs, each with its own frozen copy of the test (all the
      same, since they're created together);
    - a **test set**: N live executions of the set, one run per entry each;
    - a **test plan**: N live executions of the plan, one run per entry of every linked set.

    Every run and execution carries the batch's `batch_id` and its `batch_index` (the
    time, 1 to N). They're ordinary runs: executed by the workers like any other, readable
    through the usual run endpoints, and listed with the scope's executions (filter them in
    or out with `?batch=`). A replay of one of them is an ordinary replay, outside the batch.

    The body is the estimate's — send the same body to `POST /statistics/estimate` first to
    show the cost and the odds — plus an optional `note`. `times` left out takes the
    estimate's default. `targets` and `leave_out` are kept with the batch (shown back on
    every read, for "run again") and honoured: each check is judged at its own target, and
    a left-out check is skipped by every run and gets no verdict. `statistical_test:
    "trial"` runs a trial: no verdict, it ends `Done`. The guards are the estimate's, so
    what it shows is exactly what this creates: the scope's own run guards (404, 409),
    then the parameters, the size and the per-check choices (422).

    Every run is paid for (application calls for entries with no recorded answer, judge
    calls for LLM checks): the response's `progress.calls.*.planned` repeats the bill. A
    batch can be stopped (`POST /statistics/batches/{batch_id}/stop`) but not deleted:
    like an execution, it's history.
    """) + "\n" + _BATCH_ANATOMY


@router.post(
    path="/statistics/batches",
    summary="Run with statistics: create a batch",
    description=_CREATE_STATISTICAL_BATCH_DOC,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {
            "description": (
                "The batch and its runs are created and dispatched to the workers. It "
                "starts `Pending`; poll `GET /statistics/batches/{batch_id}` for progress "
                "and, once every run has finished, the result."
            ),
            "content": {"application/json": {"example": _BATCH_PENDING}},
        },
        404: _SCOPE_404,
        409: _SCOPE_409,
        422: {
            "description": (
                "The same validation as `POST /statistics/estimate`, every problem at once in "
                "FastAPI's list shape — plus `note` longer than 500 characters. Nothing is "
                "created."
            ),
            "content": {"application/json": {"examples": {
                "below_floor": {"summary": "Fewer times than the floor", "value":
                    _validation_error((["body", "times"],
                                       "Passes reliably needs at least 29 times with these "
                                       "settings: with fewer, no result could give an "
                                       "answer"))},
                "too_many_runs": {"summary": "More runs than a batch can create", "value":
                    _validation_error((["body", "times"],
                                       "1000 times × 11 runs each is 11000 runs, more than "
                                       "the 10000 a batch can create: at most 909 times for "
                                       "this scope"))},
            }}},
        },
    },
)
async def create_statistical_batch(
        request: BatchRequest, session: SessionDep) -> BatchDetails:  # pragma: no cover
    return await create_batch(request, session)


@router.get(
    path="/statistics/batches",
    summary="List batches",
    responses={200: {
        "description": (
            "Batches, newest first, without their per-check results (open one for those). "
            "Each has its status, progress (without the runs matrix) and, once finished, "
            "its one-sentence `summary` and its `verdicts` counts."
        ),
        "content": {"application/json": {"example": {
            "items": [{k: v for k, v in _BATCH_FINISHED.items() if k != "result"},
                      {**{k: v for k, v in _BATCH_RUNNING.items() if k != "result"},
                       "progress": {**_BATCH_RUNNING["progress"], "entries": None}}],
            "total": 2, "offset": 0, "limit": 100,
        }}},
    }},
)
async def list_statistical_batches(
        session: SessionDep,
        test_id: Annotated[uuid.UUID | None, Query(
            description="Only batches of this standalone test.")] = None,
        test_set_id: Annotated[uuid.UUID | None, Query(
            description="Only batches of this test set.")] = None,
        test_plan_id: Annotated[uuid.UUID | None, Query(
            description="Only batches of this test plan.")] = None,
        batch_status: Annotated[BatchStatusName | None, Query(
            alias="status", description="Only batches with this status.")] = None,
        offset: Annotated[int, Query(ge=0, description="Batches to skip.")] = 0,
        limit: Annotated[int, Query(ge=1, le=500, description="Batches to return.")] = 100,
) -> BatchList:  # pragma: no cover
    """The batches of a scope — the **Statistics** panel of a test, set or plan page — or
    of every scope. Filters combine.

    Any batch still `Pending` or `Running` is brought up to date before the list is
    answered, so `?status=Running` is the truth now, and a batch that finished since it
    was last read gets its result computed here (then shows its `summary`).
    """
    return await list_batches(session, offset=offset, limit=limit, test_id=test_id,
                              test_set_id=test_set_id, test_plan_id=test_plan_id,
                              status=batch_status)


_GET_STATISTICAL_BATCH_DOC = inspect.cleandoc("""
    One batch. Poll it while it runs: `progress` moves, `status` goes `Pending` →
    `Running` → its outcome, and `result` appears once every run has finished — computed
    by that read and stored, so every later read returns the same numbers.
    """) + "\n" + _BATCH_ANATOMY


@router.get(
    path="/statistics/batches/{batch_id}",
    summary="Get a batch: progress, then its result",
    description=_GET_STATISTICAL_BATCH_DOC,
    responses={
        200: {
            "description": "The batch: its progress while it runs, its result once every "
                           "run has finished. Series are shortened to three points in these "
                           "examples; a real batch has one per time.",
            "content": {"application/json": {"examples": {
                "pending": {"summary": "Just created: nothing started",
                            "value": _BATCH_PENDING},
                "running": {"summary": "Running: progress and spend, no verdict yet",
                            "value": _BATCH_RUNNING},
                "inconclusive": {"summary": "Finished: one check proven, one undecided",
                                 "value": _BATCH_FINISHED},
                "t_test": {"summary": "A t-test check, as it appears in a result",
                           "value": {**_BATCH_FINISHED, "statistical_test": "one_sample_t",
                                     "engine": "one_sample_t",
                                     "parameters": {"confidence": 0.95, "difference": 0.05,
                                                    "spread": 0.1},
                                     "status": "Passed",
                                     "summary": "Passed: the check met the goal.",
                                     "verdicts": _ONE_PROVEN,
                                     "result": {**_BATCH_FINISHED["result"],
                                                "checks_total": 1, "checks_applicable": 1,
                                                "verdicts": _ONE_PROVEN,
                                                "summary": "Passed: the check met the goal.",
                                                "entries": [{
                                                    **_BATCH_FINISHED["result"]["entries"][0],
                                                    "checks": [_T_CHECK]}]}}},
                "judge_stability": {
                    "summary": "Judge stability: the judge agreed with itself 29 times of 29",
                    "value": {**_BATCH_FINISHED, "statistical_test": "judge_stability",
                              "engine": "judge_stability",
                              "status": "Passed", "summary": "Passed: the check met the goal.",
                              "verdicts": _ONE_PROVEN,
                              "result": {**_BATCH_FINISHED["result"], "checks_total": 1,
                                         "checks_applicable": 1,
                                         "verdicts": _ONE_PROVEN,
                                         "summary": "Passed: the check met the goal.",
                                         "entries": [{
                                             **_BATCH_FINISHED["result"]["entries"][0],
                                             "recorded_answer": True,
                                             "checks": [computed.judge_stability_check()]}]}}},
                "failures_by_entry": {
                    "summary": "A set's result with failures concentrated in some entries "
                               "(entries cut to one)",
                    "value": {**_BATCH_FINISHED, "result": {
                        **_BATCH_FINISHED["result"],
                        "failures_by_entry": computed.failures_by_entry()}}},
                "stopped": {"summary": "Stopped after 12 of 30 times: the rates of what "
                                       "ran, no verdict below the floor",
                            "value": _BATCH_STOPPED},
                "no_series": {"summary": "With ?series=false",
                              "value": {**_BATCH_FINISHED, "result": {
                                  **_BATCH_FINISHED["result"],
                                  "entries": [{
                                      **_BATCH_FINISHED["result"]["entries"][0],
                                      "strip": None,
                                      "checks": [{**c, "series": None} for c in (
                                          _GATE_PASS_CHECK, _GATE_UNDECIDED_CHECK)]}]}}},
            }}},
        },
        404: _BATCH_404,
    },
)
async def get_statistical_batch(
        batch_id: uuid.UUID, session: SessionDep,
        series: Annotated[bool, Query(
            description="`false` leaves out every `series` and `strip` (the per-run points) "
                        "and the live `progress.entries`: the summaries alone, for lists "
                        "and small screens.")] = True,
) -> BatchDetails:  # pragma: no cover
    return await get_batch(batch_id, series, session)


@router.post(
    path="/statistics/batches/{batch_id}/stop",
    summary="Stop a batch",
    responses={
        200: {
            "description": (
                "The batch after stopping. Its pending runs are now `NotRan` with the reason "
                "\"Stopped before it ran: the batch was stopped\"; runs already executing "
                "finish. It's `Incomplete` with its partial result once none is running "
                "(at once when none was), `Running` until then. A batch with nothing pending "
                "comes back unchanged."
            ),
            "content": {"application/json": {"examples": {
                "stopped": {"summary": "Stopped with nothing running: Incomplete at once, "
                                       "with the partial result",
                            "value": _BATCH_STOPPED},
                "finishing": {"summary": "Stopped while two runs were executing: Running "
                                         "until they finish, no result yet",
                              "value": {**_BATCH_RUNNING,
                                        "stopped_at": "2026-10-01T09:41:07+02:00",
                                        "progress": {
                                            **_BATCH_RUNNING["progress"],
                                            "times_done": 28, "runs_done": 58,
                                            "runs": _counts(Running=2, Green=22, Amber=2,
                                                            NotRan=34),
                                            "runs_cancelled": 33, "times_cancelled": 16,
                                            "entries": [
                                                _live_entry(_ENTRY_ID, "Reset a password",
                                                            _RUN_IDS,
                                                            ["Green", "Running", "NotRan"]),
                                                _live_entry(_ENTRY_B_ID,
                                                            "Unknown order number",
                                                            _RUN_B_IDS,
                                                            ["Green", "Green", "NotRan"]),
                                            ]}}},
            }}},
        },
        404: _BATCH_404,
    },
)
async def stop_statistical_batch(
        batch_id: uuid.UUID, session: SessionDep) -> BatchDetails:  # pragma: no cover
    """Stop a batch midway — the cost is adding up, or the answer is no longer needed.
    What ran, ran; the rest never will.

    Every run of the batch still `Pending` becomes `NotRan`, in one statement. It's safe
    against the workers: a worker only claims a run while it's `Pending`, so a cancelled run
    is never executed, and a run a worker claimed first finishes and counts (a model call in
    flight can't be recalled, and it's paid for either way). `stopped_at` is set when at
    least one run was cancelled.

    The result of a stopped batch covers the runs that did complete, so the money spent
    isn't wasted: pass rates, ranges and scores always; a verdict only for a check whose
    evaluated runs still reach the test's `floor`. The status stays `Incomplete` either
    way — the batch didn't run what was asked.

    Stopping a batch that has nothing left pending changes nothing and returns it — except
    a batch that runs until there's an answer, between two waves: it releases no more
    waves, and is `Incomplete`.
    """
    return await stop_batch(batch_id, session)


# ── comparisons ──────────────────────────────────────────────────────────────

_BATCH_B_ID = "2f1b6a3e-8d4c-4b9e-a1f0-5c3d2e1b0a99"
_COMPARISON_ID = "c0ffee00-1234-4abc-9def-0123456789ab"


def _side(passed: int, lower: float, point: float, upper: float) -> dict:
    return {"counts": {"evaluated": 29, "passed": passed, "failed": 29 - passed,
                       "errored": 0, "not_ran": 0},
            "pass_rate": {"lower": lower, "point": point, "upper": upper, "method": "wilson",
                          "level": 0.95, "sides": "two"},
            "series": [_point(1, True), _point(2, passed > 27)]}


_CHECK_BETTER = {
    "label": "Mentions the reset link", "test_type": "Contains",
    "a": _side(20, 0.5077, 0.6897, 0.8272), "b": _side(28, 0.8282, 0.9655, 0.9939),
    "difference": {"lower": 0.0815, "point": 0.2759, "upper": 0.46, "method": "newcombe",
                   "level": 0.95, "sides": "two"},
    "verdict": "better",
    "reason": ("B is better: it passed 97% of the time against A's 69%. That's a real improvement, "
               "not chance (95% sure)."),
    "p_value": 0.0054, "p_value_method": "chi_square",
    "times_to_decide": None, "times_to_decide_message": None,
}

_CHECK_SAME = {
    "label": "Relevance", "test_type": "Relevance",
    "a": _side(27, 0.7804, 0.931, 0.9809), "b": _side(28, 0.8282, 0.9655, 0.9939),
    "difference": {"lower": -0.1116, "point": 0.0345, "upper": 0.1878, "method": "newcombe",
                   "level": 0.95, "sides": "two"},
    "verdict": "no_difference",
    "reason": ("No clear difference: 93% for A, 97% for B. With this many runs, a gap that small "
               "could be chance."),
    "p_value": 1.0, "p_value_method": "fisher_exact",
    "times_to_decide": 647,
    "times_to_decide_message": "Two new batches of about 647 times each would likely tell "
                               "them apart.",
}


def _compared(batch_id: str, note: str, created_at: str) -> dict:
    return {"id": batch_id, "note": note, "status": "Inconclusive",
            "statistical_test": "binomial_gate", "times_requested": 29,
            "created_at": created_at}


_COMPARISON_SUMMARY = {
    "id": _COMPARISON_ID,
    "scope": _SCOPE_SET,
    "statistical_test": "pass_rates",
    "engine": "pass_rates",
    "parameters": {"confidence": 0.95},
    "note": "Prompt v3 against v2",
    "batch_a": _compared(_BATCH_ID, "Prompt v2", "2026-09-30T16:02:11+02:00"),
    "batch_b": _compared(_BATCH_B_ID, "Prompt v3, temperature 0.2",
                         "2026-10-01T09:30:00+02:00"),
    "summary": "B is better on 1 of 2 checks, worse on none.",
    "verdicts": {"better": 1, "worse": 0, "no_difference": 1, "no_worse": 0,
                 "inconclusive": 0, "none": 0},
    "outcome": "better",
    "created_at": "2026-10-01T10:05:12+02:00",
}

_COMPARISON = {
    **_COMPARISON_SUMMARY,
    "result": {
        "verdicts": {"better": 1, "worse": 0, "no_difference": 1, "no_worse": 0,
                     "inconclusive": 0, "none": 0},
        "summary": "B is better on 1 of 2 checks, worse on none.",
        "entries": [{"entry_id": _ENTRY_ID, "test_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                     "test_set_name": "Support answers", "name": "Reset a password",
                     "checks": [_CHECK_BETTER, _CHECK_SAME]}],
        "unmatched": [],
        "paired": None,
    },
}

def _comparison_with(test: str, parameters: dict, checks: list[dict], verdicts: dict,
                     summary: str, paired: dict | None = None) -> dict:
    counts = {"better": 0, "worse": 0, "no_difference": 0, "no_worse": 0,
              "inconclusive": 0, "none": 0} | verdicts
    return {**_COMPARISON, "statistical_test": test, "engine": test, "parameters": parameters,
            "summary": summary, "verdicts": counts, "outcome": outcome(counts).value,
            "result": {"verdicts": counts, "summary": summary,
                       "entries": [{**_COMPARISON["result"]["entries"][0], "checks": checks}],
                       "unmatched": [], "paired": paired}}


_PAIRED = computed.paired_comparison()
_COMPARISON_EXAMPLES = {
    "pass_rates": {"summary": "Pass rates: one check better, one no real difference",
                   "value": _COMPARISON},
    "no_worse": {"summary": "No worse than A, by a 10-point margin: not proven",
                 "value": _comparison_with(
                     "no_worse", {"confidence": 0.95, "margin": 0.1},
                     [computed.no_worse_comparison()], {"inconclusive": 1},
                     "Can't tell yet whether B is still as good on the check.")},
    "mean_scores": {"summary": "Mean scores (Welch): ROUGE higher under B",
                    "value": _comparison_with(
                        "mean_scores", {"confidence": 0.95},
                        [computed.score_comparison(StatisticalEngine.mean_scores)],
                        {"better": 1}, "B is better on the check.")},
    "score_ranks": {"summary": "Score ranks (Mann–Whitney): ROUGE higher under B",
                    "value": _comparison_with(
                        "score_ranks", {"confidence": 0.95},
                        [computed.score_comparison(StatisticalEngine.score_ranks)],
                        {"better": 1}, "B is better on the check.")},
    "paired_entries": {"summary": "Paired by entry: seven entries, one verdict (entries and "
                                  "pairs cut)",
                       "value": {**_COMPARISON, "statistical_test": "paired_entries",
                                 "engine": "paired_entries",
                                 "summary": _PAIRED["summary"],
                                 "verdicts": all_verdicts(_PAIRED["verdicts"]),
                                 "outcome": outcome(_PAIRED["verdicts"]).value,
                                 "result": _PAIRED}},
}

_COMPARISON_404 = {
    "description": "No comparison with this id.",
    "content": {"application/json": {"example": {
        "detail": f"Statistical comparison with ID {_COMPARISON_ID} not found"}}},
}

_COMPARISON_TABLE = "\n".join("| " + " | ".join(cells) + " |" for cells in (
    (
        'Test (`engine`)',
        'Checks it reads',
        '`verdict`',
        '`difference`',
        '`p_value_method`',
    ),
    (
        '---',
        '---',
        '---',
        '---',
        '---',
    ),
    (
        '`pass_rates`',
        'every check',
        '`better` / `worse` / `no_difference`',
        "B's pass rate − A's, Newcombe's two-sided interval — it decides",
        '`chi_square`, or `fisher_exact` when an expected count is under 5',
    ),
    (
        '`no_worse`',
        'every check',
        '`no_worse` / `worse` / `inconclusive`',
        ('the same, with **one-sided** bounds (`sides: one`): `no_worse` when the lower bound '
         'is above −`margin`'),
        'null: the interval is the whole test',
    ),
    (
        '`mean_scores`',
        'scored checks',
        '`better` / `worse` / `no_difference`',
        "B's mean score − A's, Welch's t interval — it decides",
        '`welch`',
    ),
    (
        '`score_ranks`',
        'scored checks',
        '`better` / `worse` / `no_difference`',
        ('**null**: ranks have no difference to draw — `effect`, the probability a score of B '
         'beats one of A (0.5 = none), and the p-value decide'),
        '`mann_whitney_exact` or `mann_whitney_normal`',
    ),
    (
        '`paired_entries`',
        'every check',
        '**null on every check**: the one verdict is `result.paired.verdict`',
        'per check, as `pass_rates` (descriptive)',
        'per check, as `pass_rates`',
    ),
))

_COMPARISON_ANATOMY_TEXT = """
**Reading a comparison.** A is the baseline, B the change: every difference is **B − A**, so
`better` always means B did better. Five comparison tests share one shape; what changes
between them is how the per-check fields are filled:

{table}

For a scored type where lower is better (`comparison: lte`), a higher B is `worse`.

- `batch_a` / `batch_b` — each batch's id, `note` (what it was: "prompt v2", "prompt v3"),
  status, test and size, so the page can say what was compared without another call.
- `result.verdicts` counts the checks by verdict — always all six keys: `better`, `worse`,
  `no_difference`, `no_worse`, `inconclusive`, and `none` (no verdict). For
  `paired_entries` it counts the one paired verdict. `result.summary` says it in a sentence.
- `result.entries` — each entry both batches ran (matched by entry id; a standalone test is
  one entry), each check both have (matched by label), with:
  - `a` and `b` — each side's `counts` (errored and Not Ran counted apart, as in a batch),
    `pass_rate` with Wilson's two-sided interval, `scores` (a box plot's numbers) for a
    scored check, and `series` to draw the two strips or distributions over each other;
  - `difference`, `effect`, `verdict` and `p_value` as in the table. `no_difference` means
    *no real difference at this size*, not proof that there is none — `no_worse` is the
    test that proves "at most this much worse". The p-value is shown beside the verdict
    and never decides it except under `score_ranks`; name its method in the caption;
  - `verdict: null` with `reason` when there's nothing to compare: a side with no evaluated
    run, a pass/fail check under a score test, fewer than 2 scores a side (Welch) or 4
    (Mann–Whitney);
  - when undecided, `times_to_decide`: about how many times each of two **new** batches
    would need to decide it, 80% of the time (null when no size would, or more than a batch
    can run — `times_to_decide_message` says which).
- `result.paired` (`paired_entries` only) — every (entry, check) pair both batches evaluated
  (`pairs`, each `rate_a` → `rate_b`: a dot plot), the mean difference with the paired t
  interval that decides the `verdict` (6 pairs at least), and Wilcoxon's signed-rank p-value
  beside it as a check that doesn't assume bell-shaped differences; null for other tests.
- `result.unmatched` — entries or checks only one batch has (an entry added to the set
  between the batches, a check relabelled): not compared, listed so nothing goes missing.
"""
_COMPARISON_ANATOMY = _COMPARISON_ANATOMY_TEXT.format(table=_COMPARISON_TABLE)


_CREATE_COMPARISON_DOC = inspect.cleandoc("""
    **Did my change help?** Compare two finished batches of the same scope, check by check,
    with one of the comparison tests (`kind: comparison` in `GET /statistics/tests`): did B
    pass more or less often than A (`pass_rates`), is B no worse than A by more than a margin
    (`no_worse`), did a scored check's scores move (`mean_scores`, `score_ranks`), or — for a
    set — did B do better entry by entry (`paired_entries`)?

    The usual flow: run a batch (A), change something outside Assay — the application's
    prompt, its model, the judge — run another batch of the same scope (B), then compare.
    Put what changed in each batch's `note`; the comparison shows both.

    Guards, in order:
    - the two ids differ (422), and the test is a comparison test (422; see `GET
      /statistics/tests`), with its parameters in range (422);
    - both batches exist (404);
    - both have finished (409 while either has runs `Pending` or `Running` — stop it, or
      wait). `Incomplete` batches can be compared: their evaluated runs are what's compared;
    - both ran the same test, test set or test plan (422);
    - for a standalone test, the test wasn't edited between them — its input, expected
      output or checks (422 naming what changed: the runs answered different questions). A
      different *recorded answer* is allowed: that is the change being measured.

    The comparison is computed now and stored (201); read it back with `GET
    /statistics/comparisons/{comparison_id}`. Comparing does no runs and costs nothing.
    """) + "\n" + _COMPARISON_ANATOMY


@router.post(
    path="/statistics/comparisons",
    summary="Compare two batches: did my change help?",
    description=_CREATE_COMPARISON_DOC,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "The comparison, computed and stored — one example per "
                             "comparison test.",
              "content": {"application/json": {"examples": _COMPARISON_EXAMPLES}}},
        404: {"description": "Either batch doesn't exist.",
              "content": {"application/json": {"example": {
                  "detail": f"Statistical batch with ID {_BATCH_B_ID} not found"}}}},
        409: {"description": "A batch still has runs in flight.",
              "content": {"application/json": {"example": {
                  "detail": "Only finished batches can be compared: batch B is Running"}}}},
        422: {
            "description": "The request can't be compared, FastAPI's list shape, `loc` "
                           "pointing at the field.",
            "content": {"application/json": {"examples": {
                "same_batch": {"summary": "The same batch twice", "value":
                    _validation_error((["body", "batch_b"], "Compare two different batches"))},
                "scope": {"summary": "Batches of different scopes", "value":
                    _validation_error((["body", "batch_b"],
                                       "Batch B ran test set 'Billing answers', batch A test "
                                       "set 'Support answers': compare two batches of the "
                                       "same scope"))},
                "edited": {"summary": "A standalone test edited between the batches", "value":
                    _validation_error((["body", "batch_b"],
                                       "The test was edited between the two batches (the "
                                       "checks changed): their runs answered different "
                                       "questions. Compare two batches of the same "
                                       "content"))},
                "batch_test": {"summary": "A batch test instead of a comparison test",
                               "value": _validation_error((
                                   ["body", "statistical_test"],
                                   "Passes reliably isn't a comparison test; see GET "
                                   "/statistics/tests"))},
            }}},
        },
    },
)
async def create_statistical_comparison(
        request: ComparisonRequest, session: SessionDep) -> ComparisonDetails:  # pragma: no cover
    return await create_comparison(request, session)


@router.get(
    path="/statistics/comparisons",
    summary="List comparisons",
    responses={200: {
        "description": "Comparisons, newest first, without their per-check detail.",
        "content": {"application/json": {"example": {
            "items": [_COMPARISON_SUMMARY], "total": 1, "offset": 0, "limit": 100}}},
    }},
)
async def list_statistical_comparisons(
        session: SessionDep,
        batch_id: Annotated[uuid.UUID | None, Query(
            description="Only comparisons that read this batch, as A or as B.")] = None,
        test_id: Annotated[uuid.UUID | None, Query(
            description="Only comparisons of this standalone test's batches.")] = None,
        test_set_id: Annotated[uuid.UUID | None, Query(
            description="Only comparisons of this test set's batches.")] = None,
        test_plan_id: Annotated[uuid.UUID | None, Query(
            description="Only comparisons of this test plan's batches.")] = None,
        offset: Annotated[int, Query(ge=0, description="Comparisons to skip.")] = 0,
        limit: Annotated[int, Query(ge=1, le=500,
                                    description="Comparisons to return.")] = 100,
) -> ComparisonList:  # pragma: no cover
    """The comparisons of a scope (the Statistics panel's history) or of one batch (what it
    was compared with). Filters combine. Each item names both batches with their notes and
    says the outcome in `summary`; open one for the per-check detail."""
    return await list_comparisons(session, offset=offset, limit=limit, batch_id=batch_id,
                                  test_id=test_id, test_set_id=test_set_id,
                                  test_plan_id=test_plan_id)


@router.get(
    path="/statistics/comparisons/{comparison_id}",
    summary="Get a comparison",
    description=inspect.cleandoc("""
        One stored comparison, exactly as it was computed. Series are shortened to two
        points in the example; a real comparison has one per time on each side.
        """) + "\n" + _COMPARISON_ANATOMY,
    responses={
        200: {"description": "The comparison.",
              "content": {"application/json": {"examples": {
                  **_COMPARISON_EXAMPLES,
                  "no_series": {"summary": "With ?series=false", "value": {
                      **_COMPARISON, "result": {**_COMPARISON["result"], "entries": [{
                          **_COMPARISON["result"]["entries"][0],
                          "checks": [{**c, "a": {**c["a"], "series": None},
                                      "b": {**c["b"], "series": None}}
                                     for c in (_CHECK_BETTER, _CHECK_SAME)]}]}}},
              }}}},
        404: _COMPARISON_404,
    },
)
async def get_statistical_comparison(
        comparison_id: uuid.UUID, session: SessionDep,
        series: Annotated[bool, Query(
            description="`false` leaves out both sides' `series`: the numbers alone.")] = True,
) -> ComparisonDetails:  # pragma: no cover
    return await get_comparison(comparison_id, series, session)
