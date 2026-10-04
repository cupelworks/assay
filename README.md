# Assay

Test how a GenAI-powered application behaves — and keep a reproducible record of it. Bring the prompts you send your application, the outputs it produced and the outputs you expected; Assay scores each case with deterministic checks, NLP metrics against a threshold, and LLM-as-judge verdicts against a rubric, organises cases into stable test sets and plans, and records every run's outcome.

## Status

Active development. Dataset CRUD operations, test case management (create, list, get, update, delete), the test set layer (create, list, get, rename, delete), and test set entries (snapshot tests into a set, list, get, update — until the entry has been run, delete individual entries or the whole set, or unlink individual entries from the set without deleting them) are implemented. Deleting a test set cascades to its entries, but only while none of them have runs; bulk-deleting individual entries enforces the same guard and is all-or-nothing. Unlinking entries has no such guard — it's the operation for detaching a run-having entry from a set's membership without touching its frozen content or execution history. Test plans are mostly implemented (create, list, get, rename, delete, list the test sets included in a plan, link test sets to a plan, and unlink test sets from a plan — unlinking is always allowed, even if the test set already has runs recorded via this plan). Deleting a plan cascades to its own links to test sets (never blocked, mirroring the unlink behavior), but is permanently blocked once the plan has ever been executed, so a run's audit trail can never lose track of which campaign produced it. The schema-level groundwork for grouping runs by execution — live fan-out vs. replaying a specific past execution — is in place for both test plans and standalone test sets, and is now fully wired up end-to-end for both test sets and test plans (see below). Standalone runs can now be created (`POST /runs/standalone/{test_id}`) — exactly one `TestRunModel` row per call, in `Pending` status, blocked with a 409 if the test has no test types assigned. Live test-set execution can now be triggered too (`POST /runs/test-sets/{test_set_id}`) — one `TestSetExecutionModel` plus one `Pending` `TestRunModel` per entry currently in the set, blocked with a 409 if the set has no entries or if any entry has no test types assigned. A past test-set execution can now be replayed as well (`POST /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}`) — a new `TestSetExecutionModel` plus one `Pending` `TestRunModel` per entry the replayed execution ran, targeting the exact same frozen entries regardless of the set's current membership; blocked with a 404 if the execution doesn't exist or isn't linked to this test set, or a 409 if it has zero runs to replay. Live test-plan execution can now be triggered too (`POST /runs/test-plans/{test_plan_id}`) — one `TestPlanExecutionModel` plus one `Pending` `TestRunModel` per entry across every test set currently linked to the plan, blocked with a 409 if the plan has no linked test sets, if any linked test set has no entries, or if any entry has no test types assigned. A past test-plan execution can now be replayed as well (`POST /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}`) — a new `TestPlanExecutionModel` plus one `Pending` `TestRunModel` per entry the replayed execution ran, targeting the exact same frozen entries regardless of the plan's current linked test sets; blocked with a 404 if the execution doesn't exist or isn't linked to this test plan, or a 409 if it has zero runs to replay. A standalone run's own state can now be read back too: `GET /runs/standalone/{test_id}/test-runs` paginates every standalone run created for a test, and `GET /runs/standalone/{test_id}/test-runs/{test_run_id}` returns one run's full detail, including `results`/`error`/`executed_at` once populated and the frozen copy of the test the run was created from — every run, standalone included, is evaluated against a copy that later edits to the test never change. There's also a system-wide view spanning every origin: `GET /runs` paginates every run ever created — standalone, test-set-triggered, and test-plan-triggered alike, newest first — with each item's `origin` field (`Standalone`, `TestSet`, or `TestPlan`) determining which of `test_case_id`, or the `test_set_entry_id` + `test_set_execution_id`/`test_plan_execution_id` pair, is populated. `GET /runs/executions` is the equivalent system-wide view for executions rather than runs — every `TestSetExecutionModel`/`TestPlanExecutionModel` ever triggered, across every test set and test plan, newest first — but deliberately excludes standalone runs, since a standalone run has no execution wrapper to aggregate in the first place. A test set's and a test plan's past executions can now be listed as well: `GET /runs/test-sets/{test_set_id}/executions` and `GET /runs/test-plans/{test_plan_id}/executions` each paginate every execution (live or replayed) ever triggered for the set/plan, with a `run_count` rolling up how many `TestRunModel` rows it produced. The runs a specific execution produced can now be listed too, for both test sets (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs`) and test plans (`GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs`), and now a specific run's full detail can be read back for both test sets (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}`) and test plans (`GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}`), including the frozen test set entry it ran against in both cases. The test-plan version returns a nullable `test_set_id` — the entry's current test set, `null` once it's been unlinked — rather than a path parameter like the test-set version, since one plan execution spans every test set linked to the plan; both versions stay reachable even after the entry is unlinked from its set, and the test-plan one also stays reachable if that set is later unlinked from the plan, since plan-to-set links never freeze. Every run-creation endpoint now dispatches its run(s) to `assay.worker` right after creation (see [Worker](#worker)), and `execute_run` does promote a run all the way to a terminal outcome (`Green`/`Amber`/`Red`/`NotRan`) when it runs — routing each assigned type to the engine its catalogue row names and recording that engine on the result — the deterministic engines (Exact Match, Contains, Regex Match) score for real; so do every NLP metric (ROUGE, BLEU, METEOR, BERTScore, Cosine Similarity) and the LLM judges (Correctness, Relevance, Bias, Toxicity, Hallucination), with a judge model chosen under Settings. A test with no recorded `model_output` no longer sits idle: the run asks the application under test for the answer (see [Testing your application](#testing-your-application)) and every run records what it scored as `evaluated_output` with its `output_source`. Every test type in the catalogue (`GET /tests/types`) declares, as data, which evaluator engine scores it (`engine`), that engine's settings for the type (`engine_settings`), and how a threshold-scored type passes (`comparison`), with each `threshold` bounded on the metric's own native scale (BLEU 0–100, Cosine Similarity −1 to 1, the rest 0–1) — see `docs/version_1/evaluators/`. The application under test can also be configured from the UI: its settings are saved in the database (`GET`/`PATCH`/`DELETE /settings/target`, falling back to the `ASSAY_TARGET_*` environment when nothing is saved), and a check (`POST /settings/target/checks`) asks a worker to call it once and reports the answer or the reason it failed — see `docs/version_1/settings/`. The LLM judge's model is chosen the same way (`GET`/`PATCH`/`DELETE /settings/judge`, with its own checks), and `GET /health/worker` says whether runs and checks sent now will be executed. **Run with statistics** is built too (see [Running with statistics](#running-with-statistics)): a test, set or plan run N times as one batch, after an estimate of what it needs and costs; the binomial gate, the one-sample t-test and judge stability decide each check pass, fail or inconclusive at a stated confidence; the result is chart-ready and stored, with where failures concentrate; a batch can be stopped; two batches compare check by check — pass rates, no worse than a margin, mean scores, score ranks, or paired by entry. The old one-sample z-test calculator is gone (`docs/version_1/statistics/`).

Version 2.0.0 reshapes the lists for the redesigned work pages (`docs/version_2/improvement_api/`): every list of tests, sets, plans, datasets and runs can be searched, filtered, sorted and counted by facet; each item says how it stands (its latest batch, run or execution, whether it has runs, how often it was copied or linked) without another call; a test lists the sets holding it, and a set the plans linking it; an execution reads whole, its checks gathered; and tests can be made from chosen rows of a dataset, named after their prompts, with or without the recorded answers. Version 2.1.0 does the same for the batches and comparisons lists: repeatable filters, an outcome filter on comparisons, and facets for both.

## Stack

- **Python** 3.11+
- **FastAPI** for the HTTP API
- **Pydantic v2** for validation and settings
- **SQLAlchemy 2 (async)** for ORM and database access
- **Alembic** for schema migrations
- **aiosqlite** — async SQLite driver (local dev)
- **asyncpg** — async PostgreSQL driver (production)
- **Prometheus FastAPI Instrumentator** — request metrics at `GET /metrics`
- **Celery** — task queue for the run-execution worker; `execute_run` is a registered, working task (see [Worker](#worker)) — `celery[redis]` is a base dependency now (the API dispatches to it), running the worker process itself still needs the optional `worker` extra

## Quickstart

The project uses a standard `pyproject.toml`, so any tool works. With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync --extra dev
uv run uvicorn assay.main:app --reload
```

Or with plain `pip`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn assay.main:app --reload
```

Then open <http://127.0.0.1:8000/docs> for the interactive OpenAPI UI.

## Configuration

Copy `.env.example` to `.env` (in the project root) to configure the app locally. Every variable is optional — all have working defaults except the database URL in production (see [Database](#database)). `.env` is found by its location relative to `config.py`, not the process's working directory, so it's picked up correctly regardless of where a script or debugger happens to run from.

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSAY_HOST` | `127.0.0.1` | Host the dev server binds to (`main.py`'s `uvicorn.run`, used when running `python -m assay.main` directly — not consulted when running via the `uvicorn assay.main:app` CLI shown above) |
| `ASSAY_PORT` | `8000` | Port the dev server binds to, same caveat as `ASSAY_HOST` |
| `ASSAY_LOG_LEVEL` | `INFO` | Level for the app's own loggers (the `assay.*` namespace) — API, worker and Beat alike; third-party loggers stay at `INFO` regardless. `DEBUG` additionally logs every SQL statement. See [Logging](#logging) |
| `ASSAY_LOG_FORMAT` | `text` | `text`: one readable line per record, for a terminal. `json`: one JSON object per line with the fields as keys, for a log collector (Azure Monitor, Datadog, ...) — use this in production; the Docker image sets it by default. Applies to the API, the worker and Beat. See [Logging](#logging) |
| `ASSAY_DATABASE_URL` | `sqlite+aiosqlite:///./assay.db` | Database connection string — see [Database](#database) for the production format |
| `ASSAY_CORS_ALLOWED_ORIGINS` | `http://localhost:4200` | Comma-separated list of origins allowed to make cross-origin requests (e.g. `https://app.example.com,https://staging.example.com`). Credentialed requests (cookies, auth headers) are only allowed when the list isn't `*` — browsers reject `Access-Control-Allow-Credentials` paired with a wildcard origin |
| `ASSAY_JUDGE_PROVIDER` | *(unset)* | The LLM judge's provider: `anthropic`, or `openai` (also any OpenAI-compatible server through `ASSAY_JUDGE_URL`). Unset means no judge: judge checks in runs fail, saying so. The fallback until judge settings are saved from the UI (see [Judging with an LLM](#judging-with-an-llm)) |
| `ASSAY_JUDGE_MODEL` | *(unset)* | The provider's model name, e.g. `claude-sonnet-5-5` or `claude-haiku-4-5-20251001`; required with a provider. Temperature is always 0 |
| `ASSAY_JUDGE_URL` | *(unset)* | The endpoint the judge is called at, **exactly as written** — nothing is appended; unset for the provider's own (`https://api.anthropic.com/v1/messages`, `https://api.openai.com/v1/chat/completions`), or e.g. `http://localhost:11434/v1/chat/completions` for Ollama with `openai` |
| `ASSAY_JUDGE_API_KEY_ENV` | *(unset)* | The **name** of the variable holding the key, never the key; unset for the provider's standard one below |
| `ASSAY_JUDGE_TIMEOUT_SECONDS` / `ASSAY_JUDGE_MAX_RETRIES` | `60` / `2` | Per-call timeout (above 0, at most 600) and retries on connection errors, timeouts, 5xx and 429 (0 to 10) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | *(unset)* | The judge's key, read by the worker when it calls — from its environment or from `.env` — never stored or returned by the API |
| `ASSAY_TARGET_URL` | *(unset)* | The application under test — used only when a test has no recorded `model_output`: the run asks the application for the answer and scores that. Unset means no application is configured; such a run lands on `NotRan` saying so. All `ASSAY_TARGET_*` variables are the **fallback**: once the settings are saved from the UI (`PATCH /settings/target`), the saved ones apply as a whole until reset. The same rules apply to both, checked at start-up here — see [Testing your application](#testing-your-application) |
| `ASSAY_TARGET_METHOD` | `POST` | HTTP method for that call: `POST`, `PUT` or `PATCH` |
| `ASSAY_TARGET_HEADERS` | `{}` | JSON object of headers; values may reference `${ENV_VAR}`, resolved by the worker at call time from its environment or from `.env`, so a token is never part of the stored settings |
| `ASSAY_TARGET_BODY` | `{"input": "{{input}}"}` | JSON body template; `{{input}}` inside any string value is replaced by the test's `input` (inside the parsed JSON, never by pasting text into the raw body). At least one `{{input}}` is required |
| `ASSAY_TARGET_OUTPUT_PATH` | `$.output` | JSONPath to the answer in the application's JSON reply — a lookup only, never code |
| `ASSAY_TARGET_TIMEOUT_SECONDS` | `60` | Per-call timeout, above 0 and at most 600; generous by default because an application doing retrieval before its model can be slow |
| `ASSAY_TARGET_MAX_RETRIES` | `2` | Retries after the first call, with backoff (1 s, 2 s, …; `Retry-After` honoured, capped at 30 s) — only for connection errors, timeouts, 5xx and 429, never other 4xx. `0` to `10`; `0` is a single call — a retry is a second call to your application |
| `ASSAY_CELERY_BROKER_URL` | `redis://localhost:6379/0` | Read by both the worker process and the API (the API dispatches via a producer-only Celery client, see [Worker](#worker)) — see [Worker](#worker) for the full set of options (local Redis, local SQLite with nothing to install, production Azure Cache for Redis) |
| `ASSAY_CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Same scope as `ASSAY_CELERY_BROKER_URL`, see [Worker](#worker) |
| `ASSAY_WORKER_DB_POOL_SIZE` | `10` | Only read by the worker process — `assay/worker/db.py`'s sync engine pool size. Right sizing depends on the worker's `--pool`/`--concurrency` choice (see [Worker](#worker)); overridable per environment so raising it doesn't need a new build |
| `ASSAY_WORKER_DB_MAX_OVERFLOW` | `10` | Same scope as `ASSAY_WORKER_DB_POOL_SIZE`, the connections allowed beyond it under a burst |
| `ASSAY_RECONCILIATION_INTERVAL_MINUTES` | `60` | How often Celery Beat publishes the `reconcile_runs` safety-net scan (see [Worker](#worker)) |
| `ASSAY_RECONCILIATION_PENDING_THRESHOLD_MINUTES` | `15` | How old a `Pending` run must be before `reconcile_runs` treats its original dispatch as lost and re-publishes it |

## Project layout

```
src/assay/
├── main.py              # FastAPI app factory
├── config.py            # Pydantic settings (reads ASSAY_* env vars)
├── target_settings.py   # resolve_target_settings(): the saved application-under-test settings, else the environment's
├── run_check_types.py   # The check types each run asks, written beside it (shared with the worker's next waves)
├── db.py                # Async SQLAlchemy engine, session dependency, SQLite FK pragma
├── logging_config.py    # configure_logging(): stdout, text/json, request ID on every record — see Logging below
├── middleware.py        # RequestContextMiddleware: X-Request-ID in/out, one access line per request
├── exception_handlers.py # JSON 500 with request_id; 4xx/422 keep FastAPI's responses, feed the access line
├── api/                 # HTTP routes — one file per domain
│   ├── _filters.py      # The query parameters every list shares: paging, created range, search, status and membership filters
│   ├── _standing_examples.py # Swagger example fragments for how a test, set or plan stands
│   ├── datasets.py      # Dataset endpoints
│   ├── test.py          # Test case endpoints
│   ├── test_sets.py     # Test set and test set entry endpoints
│   ├── test_plan.py     # Test plan endpoints
│   ├── runs/            # Run creation and reads, split by sub-domain (too much for one file)
│   │   ├── __init__.py       # Combines the four sub-routers below into one `router`
│   │   ├── _execution_example.py # The Swagger example of one execution read whole, sets and plans alike
│   │   ├── all.py            # System-wide listings: every run across every origin (standalone, test-set-triggered, test-plan-triggered), and every execution across every test set and test plan
│   │   ├── standalone.py     # Standalone run creation, listing runs for a test, and reading a single run's details back
│   │   ├── test_sets.py      # Live/replay test-set run creation, listing a test set's past executions, listing the runs a specific execution produced, and reading one run's full detail back
│   │   └── test_plans.py     # Live/replay test-plan run creation, listing a test plan's past executions, and listing the runs a specific execution produced
│   ├── settings.py      # Settings saved from the UI: the application under test, and checks of it
│   ├── statistics.py    # Run with statistics: the catalogue, the estimate, batches (create, list, read, stop) and comparisons
│   └── meta.py          # Health check
├── schemas/             # Pydantic models — API validation and serialization
│   ├── __init__.py      # Re-exports all public models
│   ├── _common.py       # Shared base models (Pagination, RunCounts)
│   ├── standing.py      # How a test, set or plan stands: latest batch, run or execution, has_runs, counts
│   ├── listing.py       # The lists' filter values, sorts and facets
│   ├── holdings.py      # The reverse lookups: the sets holding a test, the plans linking a set
│   ├── datasets.py      # Dataset and row schemas
│   ├── test_sets.py     # Test set schemas (TestSetMetadata, PaginatedTestSetMetadataResponse, etc.)
│   ├── test_set_entries.py  # Test set entry schemas (TestSetEntryDetails, PaginatedTestSetEntriesDetails, etc.)
│   ├── test_plans.py    # Test plan schemas (TestPlanMetadata, PaginatedTestPlanMetadataResponse, etc.)
│   ├── test_plan_entries.py  # Test plan entry schemas (TestPlanEntryDetails, PaginatedTestPlanEntriesDetails)
│   ├── tests.py         # Test case schemas (CreateTestCaseRequest, ModifyTestCaseRequest, etc.)
│   ├── runs.py          # Run schemas (RunID, RunStatus, RunCreationDate, RunScores, RunError, RunExecutionDate, StandaloneRunCreationMetadata, StandaloneRunDetails, PaginatedStandaloneRunCreationMetadata, TestSetExecutionID, TestSetLiveRunCreationMetadata, TestSetReplayedExecutionID, TestSetReplayedExecutionCreationMetadata, TestSetExecutionMetadata, PaginatedTestSetExecutionMetadata, TestSetExecutionRunMetadata, PaginatedTestSetExecutionRunMetadata, TestSetExecutionRunDetails, TestPlanExecutionID, TestPlanExecutionCreationDate, TestPlanLiveRunCreationMetadata, TestPlanReplayedExecutionID, TestPlanReplayedExecutionCreationMetadata, TestPlanExecutionMetadata, PaginatedTestPlanExecutionMetadata, TestPlanExecutionRunMetadata, PaginatedTestPlanExecutionRunMetadata, RunOrigin, RunMetadata, PaginatedRunMetadata, ExecutionOrigin, ExecutionMetadata, PaginatedExecutionMetadata)
│   ├── settings.py      # TargetSettings (the one definition of the application-under-test settings and their rules), TargetSettingsUpdate, TargetSettingsRead, TargetCheckRequest, TargetCheck
│   └── statistics.py    # The catalogue, the estimate, batches and their chart-ready results, comparisons
├── services/            # Business logic — one file per operation
│   ├── _listing.py      # Listing: a list's key, joins, filters, search, sorts and facets declared once, serving the list, its total and its facets
│   ├── _standing.py     # How items stand: latest per key, has-runs questions, run counts, the SQL forms the filters use
│   ├── _scope_listing.py # The test set and test plan lists, from one factory
│   ├── datasets/
│   │   ├── _common.py                  # Shared helpers (_get_dataset_or_404, _get_rows_or_404, etc.)
│   │   ├── get_datasets_metadata.py
│   │   ├── get_dataset_rows.py
│   │   ├── upload_full_dataset.py
│   │   ├── upload_rows_in_dataset.py
│   │   ├── replace_dataset_content.py
│   │   ├── update_dataset_name.py
│   │   ├── update_dataset_rows.py
│   │   ├── delete_full_dataset.py
│   │   └── delete_dataset_rows.py
│   ├── test_sets/
│   │   ├── _common.py                  # Shared helpers (_find_test_set_or_404, _find_test_sets_or_404, _find_test_set_entry_in_specific_test_set_or_404, _check_test_set_entry_has_no_runs_or_409, _check_test_set_entries_have_no_runs_or_409, etc.)
│   │   ├── create_test_set.py
│   │   ├── get_test_sets_metadata.py   # Paginated listing and single fetch by ID
│   │   ├── add_tests_to_test_set.py    # Snapshot tests into a set as entries
│   │   ├── get_test_sets_entries.py    # Paginated listing and single fetch of entries
│   │   ├── update_entry.py             # Partial update of an entry, until it has been run
│   │   ├── update_test_set.py          # Rename, with a self-name no-op guard around the uniqueness check
│   │   └── delete_test_set.py          # Delete a whole set (cascades to entries), bulk-delete specific entries (blocked while any target entry has runs), or unlink specific entries (no runs guard — that's the point)
│   ├── test_plans/
│   │   ├── _common.py                  # Shared helpers (_check_unique_test_plan_name_or_409, _find_test_plan_by_id_or_404, _check_test_set_not_in_test_plan_or_409, _find_test_plan_entries_or_404, _check_test_plan_has_no_runs_or_409)
│   │   ├── create_test_plan.py
│   │   ├── get_test_plans_metadata.py         # Paginated listing and single fetch by ID
│   │   ├── get_test_plan_entries_metadata.py  # Paginated listing of the test sets included in a plan
│   │   ├── update_test_plan.py                # Rename, with a self-name no-op guard around the uniqueness check
│   │   ├── add_test_sets_to_test_plan.py      # Link one or more test sets to a plan, deduplicated via the existence-check query
│   │   ├── remove_test_set_from_test_plan.py  # Unlink one or more test sets from a plan — all-or-nothing; unconditional even if the test set already has runs via this plan
│   │   └── delete_test_plan.py                # Delete a plan (cascades to its own links to test sets) — permanently blocked once it has ever been executed
│   ├── tests/
│   │   ├── _common.py                  # Shared helpers (_find_all_tests_or_404, _find_test_by_id_or_404, _validate_test_type_name)
│   │   ├── create_new_test.py          # Manual creation and bulk creation from dataset
│   │   ├── get_tests.py                # Paginated listing and single fetch by ID
│   │   ├── update_test.py              # Partial update with field-level null semantics
│   │   └── delete_test.py              # Bulk delete with referential integrity checks
│   ├── runs/
│   │   ├── _common.py                  # Shared helpers (_find_test_set_entries_ids_or_409, _find_test_sets_entries_ids_or_409, _check_tests_have_test_types_or_409, _check_test_set_entries_have_test_types_or_409, _check_test_set_execution_or_404, _check_test_set_execution_id_linked_to_specific_test_set_id_or_404, _find_test_set_execution_id_entries_or_409, _find_test_plan_entries_or_409, _check_test_plan_execution_or_404, _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404, _find_test_plan_execution_id_entries_or_409, _check_test_run_by_id_or_404, _check_test_run_id_linked_to_specific_test_id_or_404 — use-case guards, not test_sets/test_plans-domain ones; existence checks themselves stay in test_sets/_common.py and test_plans/_common.py)
│   │   ├── create_new_run.py           # Create a standalone pending run with its own copy of the test (one run regardless of test type count, 409 if none assigned), trigger a live test-set run (one execution + one row per entry, 409 if the set has no entries or if any entry has no test types assigned), replay a past test-set execution (one new execution + one row per entry the replayed execution ran, 404 if the execution doesn't exist or isn't linked to this set, 409 if it has zero runs), trigger a live test-plan run (one execution + one row per entry across every linked test set, 409 if the plan has no linked test sets, a linked set has no entries, or any entry has no test types assigned), or replay a past test-plan execution (one new execution + one row per entry the replayed execution ran, 404 if the execution doesn't exist or isn't linked to this plan, 409 if it has zero runs)
│   │   ├── _summaries.py               # What a list shows of a run: its test's and scope's names, its checks counted, the worst-first rank
│   │   ├── list_runs.py                # GET /runs and its facets
│   │   ├── executions.py               # One execution read whole, and its runs sorted
│   │   ├── get_run_metadata.py         # Paginated listing of every standalone run created for a test, every execution triggered for a test set or test plan, or every run a specific test-set/test-plan execution produced
│   │   └── get_run_details.py          # Full detail for a single standalone run or a single test-set-execution run, including results/error/executed_at once populated
│   ├── settings/
│   │   ├── _common.py                  # Shared helpers (_find_target_row, _apply_or_422 — the fields sent applied to the settings in effect, validated as a whole)
│   │   ├── get_target_settings.py      # The settings in effect and their source (database or environment)
│   │   ├── update_target_settings.py   # PATCH: apply, validate, save the complete row
│   │   ├── reset_target_settings.py    # DELETE: drop the row, the environment applies again
│   │   ├── create_target_check.py      # Store a check with the settings to check, publish check_target by name
│   │   └── get_target_check.py         # A check as far as it has got
│   └── statistics/
│       ├── catalogue.py                # The statistical tests as code: parameters, floors, sizing, limits
│       ├── _scope.py                   # A test, set or plan resolved into entries and checks, with the run guards
│       ├── estimate.py                 # What a batch would need and cost, creating nothing
│       ├── create_batch.py             # N runs or executions in one transaction, dispatched
│       ├── _batches.py                 # Loading a batch's runs, progress and spend, the refresh that computes the result on read
│       ├── compute.py                  # Pure: the fold of runs per check, the verdicts, the roll-up to a batch status
│       ├── get_batch.py / list_batches.py / stop_batch.py
│       ├── compare.py                  # Pure: two batches compared check by check (Newcombe's interval of B − A)
│       └── create_comparison.py / get_comparison.py / list_comparisons.py
├── stats_math.py        # The statistics' arithmetic on the standard library: exact binomial, Wilson, Student's t, chi-square, Fisher, Newcombe, sample sizes
├── models/              # SQLAlchemy ORM models — database table definitions
│   ├── __init__.py
│   ├── base.py          # Shared DeclarativeBase
│   ├── datasets.py      # DatasetModel, DatasetRowModel
│   ├── settings.py      # SettingsModel (one JSON row per group of settings), TargetCheckModel
│   ├── statistics.py    # StatisticalBatchModel (with BatchStatus), StatisticalComparisonModel
│   └── test.py          # TestModel, TestSetModel, TestSetEntryModel, TestSetExecutionModel,
│                        # TestPlanModel, TestPlanEntryModel, TestPlanExecutionModel,
│                        # TestRunModel, StandaloneRunModel (a standalone run's own copy
│                        # of its test), and related junction tables
└── worker/              # Celery app — see Worker below
    ├── __init__.py      # Re-exports `app` so `celery -A assay.worker worker` resolves it
    ├── celery_app.py    # Celery() instance, broker/backend from settings, rediss:// TLS handling,
    │                    # include=[...] listing every task module to import at startup
    ├── db.py            # Sync SQLAlchemy engine/session for tasks — separate from assay/db.py's
    │                    # async one; fork-safe via a worker_process_init signal that disposes
    │                    # the engine per child process
    ├── tasks/           # Thin @app.task wrappers (Celery plumbing only): acquire a session, delegate
    │   ├── execute_run.py   # Execute one run (published by every run-creation endpoint)
    │   ├── reconcile_runs.py # Beat's safety net: re-publish Pending runs whose dispatch was lost
    │   └── check_target.py  # One call to the application under test, for a check from the UI
    ├── services/        # The unit-tested logic behind each task
    │   ├── execute_run.py    # Claim, resolve content, get the answer (recorded, or from the
    │   │                     # application with the settings in effect), evaluate, roll up, write back
    │   ├── reconcile_runs.py
    │   └── check_target.py   # Claim, expire if stale, one call, write the outcome
    ├── target.py        # The adapter that calls the application under test, given its settings
    └── evaluators/      # registry.py dispatches each assigned type to the engine its catalogue
        │                # row names; engines/ holds one module per engine
        └── _common.py        # threshold/comparison helpers the scoring engines share
alembic/                 # Alembic migration environment
alembic.ini              # Alembic configuration (URL is read from ASSAY_DATABASE_URL at runtime)
tests/                   # Pytest suite mirroring src/assay/services/
```

## API surface

**Lists.** Every list takes `offset` and `limit` and returns `total`, counting everything within its filters. A filter given several times means any of its values (`?latest_run=Red&latest_run=NotRan`); different filters narrow together. `q` searches, ignoring case: give it once per phrase, and every phrase must be found, in any order. `created_from`/`created_to` take ISO 8601 moments (with an offset, or taken as UTC without one). Every timestamp is stored and sent in UTC (`2026-09-27T12:36:59.928077Z`). The lists of tests, test sets, test plans, datasets, runs, batches and comparisons take `limit` 1–500 and have a `/facets` sibling: the same filters, each filter's values counted within every *other* filter chosen, so a facet's count is what the list's `total` would be with that value picked; `created_edges` (repeatable) adds a `created` facet counting from each edge sent, plus `before`.

### Health
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{"status": "ok"}` |
| `GET` | `/health/worker` | Whether runs and checks sent now will be executed: `status` `ready`, `no_worker`, `outdated` (a worker runs older code than the API — its code fingerprint differs, it's too old to report one, or it lacks a task the API sends; restart it) or `broker_unreachable`, with the broker's reachability and, per answering worker, its version, code fingerprint, `current_code`, missing tasks and a one-sentence `problem`. Always 200; about 1 s, at most about 3 |

### Datasets
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/datasets` | List the datasets, each with `row_count` and `first_prompt`; filters `made_into_tests` (`true`/`false`), `rows` (`0`, `1-10`, `11-100`, `101+`, repeatable), a created range, `q` (name and first prompt); `sort` `newest` (default), `oldest`, `name` |
| `GET` | `/datasets/facets` | The datasets within the filters counted by `made_into_tests`, `rows` (`0`, `1-10`, `11-100`, `101+`) and, with `created_edges`, `created` |
| `GET` | `/datasets/{dataset_id}` | Retrieve metadata for a single dataset, with `row_count` and `first_prompt` |
| `GET` | `/datasets/{dataset_id}/rows` | List a dataset's rows by `number` (1, 2, … within the dataset, kept as rows are added; gaps after a delete), each with `test_count` (tests made from it); `q` searches prompt, expected and model output; `offset`/`limit` optional |
| `POST` | `/datasets/path` | Create a dataset from a local `.jsonl` file |
| `POST` | `/datasets/rows` | Append rows to an existing dataset |
| `PUT` | `/datasets/rows` | Replace all rows in a dataset — tests created from the replaced rows are unaffected, only their traceability pointer is cleared |
| `PATCH` | `/datasets/name` | Rename a dataset |
| `PATCH` | `/datasets/rows` | Update existing rows by ID |
| `DELETE` | `/datasets` | Delete a dataset and all its rows — tests created from those rows are unaffected, only their traceability pointer is cleared |
| `DELETE` | `/datasets/rows` | Delete specific rows by ID — same traceability-clearing behavior as above |

### Test cases
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tests/types` | The test type catalogue for one `test_category` — each type's name, guidance, the `config_fields` an assignment must supply (a `threshold`'s `min`/`max` are that type's own score range), and, read-only, how the worker evaluates it: `engine`, `engine_settings`, `comparison` |
| `GET` | `/tests` | List the tests, each with how it stands: `created_at`, `dataset_row_id`/`dataset_row_number`, `latest_batch`, `latest_run` (newest standalone run outside a batch), `has_runs`, `copy_count`, `test_set_count`. Filters `check_type`, `latest_verdict` (a batch status or `none`), `latest_run` (a run status or `never`), `has_recorded_answer`, `in_test_set` (`any`, `none` or a set's id), `from_dataset`, a created range, `q` (name, input, checks' labels and types); `sort` `latest_activity` (default), `name`, `created`, `dataset_row` (row-number order, a dataset's tests together, tests from no row last) |
| `GET` | `/tests/facets` | The tests within the filters counted by each filter's values (`check_type`, `latest_verdict`, `latest_run`, `has_recorded_answer`, `in_test_set`, `from_dataset`, `created`) |
| `GET` | `/tests/{test_case_id}` | Retrieve a single test case by ID, standing included as in the list |
| `GET` | `/tests/{test_case_id}/test-sets` | The test sets holding a copy of the test, by name, each as the sets list shows it, with its copy (`entry`: `has_runs`, `matches_test` — whether it still asks what the test asks now, the name not compared); `unlinked_copies` counts copies whose set link was removed. 404 for an unknown test |
| `POST` | `/tests` | Create a single test case manually; a type may be assigned more than once, each assignment told apart by its `label` (defaults to the type's name, numbered when taken) |
| `POST` | `/tests/from-dataset` | Bulk-create test cases from a dataset's rows, in row-number order — all of them, or only `row_ids` (a row not in the dataset is a 422 naming it); `naming` `numbered` ("New Test <n>", default) or `prompt` (the row's prompt, at most 60 characters, cut at a word with "…"); `recorded_answers` `keep` (default; a blank `model_output` means none, so its runs ask the application) or `leave_out` (every run asks the application) |
| `PATCH` | `/tests/{test_case_id}` | Partially update a test case — only sent fields are changed; `expected_output`/`model_output` sent as `null` are cleared, `name`/`input` sent as `null` are left as they are; unknown fields are rejected |
| `DELETE` | `/tests` | Delete test cases by ID (guards against linked test sets and test runs) |

### Test sets
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/test-sets` | List the test sets, each with `entry_count`, `test_plan_count` and how it stands: `latest_batch`, `latest_execution` (with its runs by status), `execution_count`, `has_runs`. Filters `latest_verdict`, `latest_run` (its newest execution's outcome: `Running` while any run is Pending or Running, else the first of `NotRan`, `Red`, `Amber`, `Green`; `never`), `in_test_plan` (`any`, `none` or a plan's id), `holds_test`, a created range, `q` (name); `sort` `latest_run` (default, never run last), `created`, `name` |
| `GET` | `/test-sets/facets` | The test sets within the filters counted by `latest_verdict`, `latest_run`, `in_test_plan`, `holds_test`, `created` |
| `GET` | `/test-sets/{test_set_id}` | Retrieve metadata for a single test set, standing included as in the list |
| `GET` | `/test-sets/{test_set_id}/test-plans` | The test plans linking the set, by name, each as the plans list shows it, with the plan's `entry_id`. 404 for an unknown set |
| `POST` | `/test-sets` | Create a new test set (name must be unique) |
| `PATCH` | `/test-sets/{test_set_id}` | Rename a test set — resubmitting its current, unchanged name is a no-op, not a 409 |
| `DELETE` | `/test-sets/{test_set_id}` | Delete a test set and all of its entries — blocked with a 409 if any entry has runs |
| `GET` | `/test-sets/{test_set_id}/entries` | List all entries (snapshotted tests) in a test set (paginated), each with `has_runs` (then it's frozen) |
| `GET` | `/test-sets/{test_set_id}/entries/{entry_id}` | Retrieve a single entry by ID |
| `POST` | `/test-sets/{test_set_id}/entries` | Snapshot one or more tests into a test set as entries |
| `PATCH` | `/test-sets/{test_set_id}/entries/{entry_id}` | Partially update an entry, with the same `null` rules as a test case — only allowed until it has been run at least once (409 otherwise) |
| `DELETE` | `/test-sets/{test_set_id}/entries` | Bulk-delete one or more entries by ID — all-or-nothing, blocked with a 409 if any target entry has runs |
| `PATCH` | `/test-sets/{test_set_id}/entries` | Bulk-unlink one or more entries by ID (clears `test_set_id`, entry row and any runs left untouched) — all-or-nothing, no runs guard, unlike the sibling `DELETE` |

### Test plans
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/test-plans` | List the test plans, each with `linked_set_count` and how it stands, as the sets do. Filters `latest_verdict`, `latest_run`, `holds_test_set`, a created range, `q` (name); `sort` `latest_run` (default), `created`, `name` |
| `GET` | `/test-plans/facets` | The test plans within the filters counted by `latest_verdict`, `latest_run`, `holds_test_set`, `created` |
| `GET` | `/test-plans/{test_plan_id}` | Retrieve metadata for a single test plan, standing included as in the list |
| `POST` | `/test-plans` | Create a new test plan (name must be unique) |
| `PATCH` | `/test-plans/{test_plan_id}` | Rename a test plan — resubmitting its current, unchanged name is a no-op, not a 409 |
| `DELETE` | `/test-plans/{test_plan_id}` | Delete a test plan and its links to test sets — permanently blocked with a 409 once the plan has ever been executed |
| `GET` | `/test-plans/{test_plan_id}/entries` | List the test sets included in a test plan (paginated); use each item's `test_set.id` with `GET /test-sets/{test_set_id}/entries` to fetch that set's snapshotted tests |
| `POST` | `/test-plans/{test_plan_id}/entries` | Link one or more test sets to a test plan — blocked with a 409 if any is already linked to this plan |
| `DELETE` | `/test-plans/{test_plan_id}/entries` | Unlink one or more test sets from a test plan — all-or-nothing (404 if any requested test set isn't linked to this plan); always allowed even if the test set already has runs recorded via this plan |

### Runs
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/runs/standalone/{test_id}` | Create a standalone, pending run for a live test — with its own frozen copy of the test, so later edits never change what the run was judged against — then dispatch it for execution (best-effort — the row stays `Pending` if dispatch fails). Blocked with a 409 if the test has no test types assigned |
| `POST` | `/runs/test-sets/{test_set_id}` | Trigger a live execution of a test set — one pending run per entry currently in the set, grouped under a new test set execution, each then dispatched for execution (best-effort). Blocked with a 409 if the set has no entries or if any entry has no test types assigned |
| `POST` | `/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}` | Replay a past test set execution — one pending run per entry that execution ran, targeting the exact same frozen entries regardless of the set's current membership, grouped under a new test set execution, each then dispatched for execution (best-effort). Blocked with a 404 if the execution doesn't exist or isn't linked to this test set, or a 409 if it has zero runs to replay |
| `POST` | `/runs/test-plans/{test_plan_id}` | Trigger a live execution of a test plan — one pending run per entry across every test set currently linked to the plan, grouped under a new test plan execution, each then dispatched for execution (best-effort). Blocked with a 409 if the plan has no linked test sets, if any linked test set has no entries, or if any entry has no test types assigned |
| `POST` | `/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}` | Replay a past test plan execution — one pending run per entry that execution ran, targeting the exact same frozen entries regardless of the plan's current linked test sets, grouped under a new test plan execution, each then dispatched for execution (best-effort). Blocked with a 404 if the execution doesn't exist or isn't linked to this test plan, or a 409 if it has zero runs to replay |
| `GET` | `/runs` | Every run in the system, whatever its origin — `id`, `status`, `created_at`, `origin`, the origin's IDs (`test_case_id` on every run: for a set's or plan's run, the test its entry was copied from; `test_set_entry_id` + `test_set_execution_id` + `test_set_id`, or `test_set_entry_id` + `test_plan_execution_id` + `test_plan_id`), `scope_name`, and what a list shows without opening it: `test_name`, `checks` (`met`, `total`, the labels `not_met`; null until it ran) and `error`. Filters `status`, `origin`, `batch`, `test_set_id`, `test_plan_id`, `check_type`, a created range, `q` (test name, set's or plan's name); `sort` `newest` (default) or `oldest` |
| `GET` | `/runs/facets` | The runs within the filters counted by `status` and `origin` |
| `GET` | `/runs/executions` | Paginated list of every execution across every test set and test plan (live or replayed) — `id`, `created_at`, `origin` (`TestSet`/`TestPlan`), `run_count`, plus the origin-specific ID(s) (`test_set_id`/`test_plan_id`, and `replayed_test_set_execution_id`/`replayed_test_plan_execution_id` if it was a replay), `name` (its set's or plan's) and `runs` (its runs by status) per item, newest first |
| `GET` | `/runs/standalone/{test_id}/test-runs` | Paginated list of every standalone run ever created for a test — `id`, `status`, `created_at`, `test_case_id` per item, newest first |
| `GET` | `/runs/standalone/{test_id}/test-runs/{test_run_id}` | Full detail for a single standalone run — `id`, `status`, `created_at`, `test_case_id`, plus `results`/`error`/`executed_at`, which stay null until the run reaches a terminal status, plus the run's frozen copy of the test (`name`, `input`, `expected_output`, `model_output`, `test_type_assignments`, `test_case_snapshot_at`) |
| `GET` | `/runs/test-sets/{test_set_id}/executions` | Paginated list of every execution (live or replayed) ever triggered for a test set — `id`, `created_at`, `test_set_id`, `run_count`, `replayed_execution_id`, `runs` (by status) per item, newest first |
| `GET` | `/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}` | One execution read whole: the set's `name`, its runs by status, and its checks over the runs that finished — `met`, each one `not_met` with its run, test and label, and each run that couldn't run (`not_ran`) with its `error` and how many `checks` it would have asked. Same 404s as its runs list |
| `GET` | `/runs/test-plans/{test_plan_id}/executions` | Paginated list of every execution (live or replayed) ever triggered for a test plan — `id`, `created_at`, `test_plan_id`, `run_count`, `replayed_execution_id`, `runs` (by status) per item, newest first |
| `GET` | `/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}` | The same read for a test plan's execution |
| `GET` | `/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs` | Paginated list of every run a specific test set execution produced — `id`, `status`, `created_at`, `test_set_entry_id`, `test_set_execution_id`, `output_source`, `test_name`, `checks` and `error` per item; `sort` `newest` (default), `worst_first` (NotRan, Red, Amber, Green, Running, Pending, then by test name) or `name`. 404 if the test set or execution doesn't exist, or the execution isn't linked to this test set |
| `GET` | `/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}` | Full detail for a single run produced by a test set execution, including `results`/`error`/`executed_at` and the frozen test set entry it ran against (`test_case_id`, `name`, `input`, `expected_output`, `model_output`, `test_type_assignments`, `test_case_snapshot_at`). 404 if the test set, execution, or run doesn't exist, or if any of them aren't linked to the one above it in the path |
| `GET` | `/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs` | Paginated list of every run a specific test plan execution produced — `id`, `status`, `created_at`, `test_set_entry_id`, `test_plan_execution_id`, `output_source`, `test_name`, `checks` and `error` per item; `sort` `newest` (default), `worst_first` (NotRan, Red, Amber, Green, Running, Pending, then by test name) or `name`. 404 if the test plan or execution doesn't exist, or the execution isn't linked to this test plan |
| `GET` | `/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}` | Full detail for a single run produced by a test plan execution, including `results`/`error`/`executed_at` and the frozen test set entry it ran against (`test_case_id`, `name`, `input`, `expected_output`, `model_output`, `test_type_assignments`, `test_case_snapshot_at`). Also returns `test_plan_id` and a nullable `test_set_id` — the entry's *current* test set, `null` if it's since been unlinked; unlike the test-set version, there's no `test_set_id` in the path, since one plan execution spans every test set linked to the plan. 404 if the test plan, execution, or run doesn't exist, or if any of them aren't linked to the one above it in the path |

### Settings
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/settings/target` | The application-under-test settings in effect (`url`, `method`, `headers`, `body`, `output_path`, `timeout_seconds`, `max_retries`), plus `source` — `database` (saved from the UI) or `environment` (the `ASSAY_TARGET_*` fallback) — and `updated_at`. Header values come back as stored: a `${NAME}` reference is never expanded |
| `PATCH` | `/settings/target` | Change some settings; only the fields sent change (`url: null` unsets it), `headers`/`body` replaced whole. The result is validated as a whole — 422 listing every problem, nothing saved — then saved as the complete group; the first save carries the environment's settings forward. Applies from the next run, no worker restart |
| `DELETE` | `/settings/target` | Reset: drop the saved settings so the environment's apply again; returns them. Not an error when nothing is saved |
| `POST` | `/settings/target/checks` | Ask a worker to call the application once with `input`, using the settings in effect plus any proposed `settings` (validated like a PATCH, never saved). 202 with the check, `pending`; `completed` at once with `ok: false` if it couldn't be sent to a worker. 422 for invalid proposed settings |
| `GET` | `/settings/judge` | The LLM judge's settings in effect (`provider`, `model`, `url`, `api_key_env`, `timeout_seconds`, `max_retries`), plus the read-only `endpoint` (the URL the worker calls: `url` as written, else the provider's own) and `api_key_variable` (the variable the worker reads the key from), `source` and `updated_at`. The key itself is never returned |
| `PATCH` | `/settings/judge` | Change some judge settings; only the fields sent change, `null` clears a nullable one; validated as a whole (a provider needs a model), 422 listing every problem, saved as the complete group |
| `DELETE` | `/settings/judge` | Reset the judge settings to the environment's; returns them |
| `POST` | `/settings/judge/checks` | Ask a worker to put one fixed question to the judge with the settings in effect plus any proposed `settings` (never saved). 202 with the check, `pending`; same lifecycle as the application checks |
| `GET` | `/settings/judge/checks/{check_id}` | A judge check as far as it has got: `completed` with `ok` (a readable verdict came back), `answer` (the judge's rationale) or `error` (e.g. `ANTHROPIC_API_KEY is not set on this server`). 404 if it doesn't exist |
| `GET` | `/settings/target/checks/{check_id}` | A check as far as it has got: `pending`, `running`, or `completed` with `ok`, `status_code`, `latency_ms`, and the `answer` or the `error` (the same reason a run would get). A check queued over 5 minutes completes as expired without calling the application. 404 for an unknown ID |

### Run with statistics
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/statistics/tests` | The catalogue of statistical tests, one per `statistical_tests` row — seeded: batch tests `binomial_gate`, `one_sample_t`, `judge_stability`, `trial`, and the three that run until there's an answer, `sequential_gate`, `sequential_t`, `sequential_judge_stability`; comparisons `pass_rates`, `no_worse`, `mean_scores` (Welch), `score_ranks` (Mann–Whitney), `paired_entries`. Each row names the `engine` in code that does its arithmetic (a new test on an existing engine, with other defaults, is a row) and gives its question, kind (`batch` or `comparison`), what it reads, which checks it applies to, its parameters (default and range), `engine_settings`, its floor (the fewest times it can conclude at, with the kind of floor, formula, explanation and worked examples) and method; plus `max_times` and `max_runs` per batch |
| `POST` | `/statistics/estimate` | What a batch would need and cost, creating nothing: the floor and why, the sizes worth offering (one `default`), the binomial gate's rule at `times`, runs and calls (application, judge) per time and in total, every entry with which checks get a verdict, and `warnings` (recorded answers, no judge or application configured). Same guards as a batch: 404/409 from the scope, 422 for parameters, times below the floor or over the limits; planned from each check's history: every size's chance that every check gets an answer, the size worth paying for (90% goal, or the best within 5 points), each check's outlook, the check driving the cost and what would make it cheaper (a check's own target, leaving it out, less certainty), the same for running until there's an answer, or a trial when a check has never run. Takes `targets` and `leave_out` per check |
| `POST` | `/statistics/batches` | Run a test, test set or test plan N times as one batch — N standalone runs, or N live executions — each carrying `batch_id` and `batch_index`, all dispatched to the workers. 202 with the batch, `Pending`. The estimate's body plus an optional `note`; the estimate's guards; `targets`/`leave_out` honoured (left-out checks skipped by the runs). A trial gives no verdict and ends `Done`; until there's an answer, `times` is the maximum and it runs in waves, stopping as soon as every check has its answer |
| `GET` | `/statistics/batches` | Batches newest first, without per-check detail: status, progress, the outcome's `summary` and `verdicts` counts. Filters `test_id`, `test_set_id`, `test_plan_id`, `status`, each repeatable; in-progress batches are brought up to date first |
| `GET` | `/statistics/batches/facets` | The batches within the same filters counted by `status` (all eight) and by test, set and plan id |
| `GET` | `/statistics/batches/{batch_id}` | One batch: `progress` (times and runs done, runs by status, application and judge calls planned/finished/in flight, and the runs matrix so far in `entries`) while it runs; once every run has finished, the `result` — computed by that read and stored — per entry and per check: counts (errored and Not Ran apart), pass rate with its interval, score summary, the verdict with the interval it used, p-values, the gate's rule, and `times_to_decide`; and the per-run `series` for charts (`?series=false` leaves them out). Status `Pending` → `Running` → `Passed`/`Failed`/`Inconclusive`/`Incomplete`/`NotRan` |
| `POST` | `/statistics/batches/{batch_id}/stop` | Cancel the batch's `Pending` runs (`NotRan`, "Stopped before it ran: the batch was stopped"); running ones finish and count. `Incomplete` once none is running; a batch with nothing pending is returned unchanged |
| `POST` | `/statistics/comparisons` | Compare two finished batches of the same scope, check by check — A the baseline, B the change, every difference B − A — with a comparison test: `pass_rates` (Newcombe's interval of the pass rates decides `better`/`worse`/`no_difference`; chi-square's or Fisher's p-value beside it), `no_worse` (one-sided bounds against a `margin`: `no_worse`/`worse`/`inconclusive`), `mean_scores` (Welch) and `score_ranks` (Mann–Whitney, with `effect`) for scored checks, or `paired_entries` (one verdict over every entry and check, in `result.paired`). Per check: each side's counts, pass rate, scores and series, the difference with its interval, the verdict, the p-value and, when undecided, the batch size that would decide it; entries or checks only one batch has listed as `unmatched`. 201, stored. 404 unknown batch, 409 a batch still running, 422 different scopes or a standalone test edited between them (a different recorded answer is allowed) |
| `GET` | `/statistics/comparisons` | Comparisons newest first, without per-check detail: each with its `summary`, its checks counted by verdict (`verdicts`) and one overall `outcome` (`worse`, `better`, `no_worse`, `inconclusive`, `no_difference` or `none`); filters `batch_id` (as A or B), and, repeatable, `test_id`, `test_set_id`, `test_plan_id`, `outcome` |
| `GET` | `/statistics/comparisons/facets` | The comparisons within the same filters counted by `outcome` (all six) and by test, set and plan id |
| `GET` | `/statistics/comparisons/{comparison_id}` | One comparison as stored; `?series=false` leaves out both sides' series |

Every run and execution listing (`/runs`, `/runs/executions`, a test's runs, a set's or
plan's executions) returns `batch_id`/`batch_index` and takes `?batch=none` (only what no
batch created) or `?batch=<batch_id>`; an execution's runs and every run's detail carry
the two fields too.

## Database

The database URL is controlled by `ASSAY_DATABASE_URL`. No code changes are needed between environments — only the URL changes.

| Environment | URL format |
|-------------|------------|
| Local (default) | `sqlite+aiosqlite:///./assay.db` |
| Production | `postgresql+asyncpg://user:pass@host:5432/assay` |

See [Configuration](#configuration) for how to set this via `.env`.

SQLite foreign key enforcement is enabled automatically on every connection via a `PRAGMA foreign_keys=ON` hook in `db.py`. This is required for `ON DELETE CASCADE` to work in SQLite.

Deleting a test set relies on this: its entries cascade-delete at the database level (`test_set_entries.test_set_id` has `ON DELETE CASCADE`, and the ORM relationship uses `passive_deletes=True` so it lets the database do it rather than nulling the FK itself). The `test_runs.test_set_entry_id` FK has no cascade, so if an entry still has a run, the database refuses the delete outright — a backstop behind the service-layer 409 check.

Deleting or replacing dataset rows relies on a different FK action: `tests.dataset_row_id` has `ON DELETE SET NULL`. A test copies its own `input`/`expected_output`/`model_output` at creation time and never reads through this FK again, so there's nothing to protect by blocking the delete — deleting the source row (via `DELETE /datasets`, `DELETE /datasets/rows`, or a `PUT /datasets/rows` replace) just clears the test's traceability pointer instead.

`statistical_tests` is a seeded reference table like `test_types`: a row names the engine in code that does a statistical test's arithmetic and carries its texts, parameter defaults and settings. A batch records its row's `id` (`statistical_test`) and that row's `engine` as plain text, with no foreign key to the row — a batch is history, and it is finished with the engine it recorded, so a row renamed, re-pointed or deleted later never changes a stored batch; it only stops new batches of that test.

`test_set_entries.test_set_id` is nullable, which is what makes unlinking possible: unlinking sets it to `NULL` directly rather than deleting the row, detaching the entry from the set while leaving the row (and any runs pointing at it) in place.

Deleting a test plan relies on the same cascade mechanism as test sets, applied to a different table: `test_plan_entries.test_plan_id` has `ON DELETE CASCADE`, and `TestPlanModel.entries` uses `passive_deletes=True` so the database removes the plan's links rather than the ORM trying to null out their (`NOT NULL`) `test_plan_id` first. `test_plan_executions.test_plan_id`, by contrast, has no `ondelete` at all (default `RESTRICT`) — deliberately, since a plan that's ever been executed is blocked from deletion by a service-layer 409 before the delete is ever attempted; the FK is a backstop behind that guard, the same relationship the existing `test_runs.test_set_entry_id` restrict has to the entry-deletion 409 check.

### Migrations

With uv:
```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic downgrade -1
```

With pip (activate your venv first):
```bash
alembic upgrade head
alembic revision --autogenerate -m "describe the change"
alembic downgrade -1
```

## Testing your application

Assay tests an application, not a bare model: what it scores is the application's answer — its model plus its system prompt, retrieval, tools and post-processing. A test can carry that answer already (`model_output`), and then the run scores it as-is. When a test has **no** recorded answer, the run obtains one from your application's own endpoint, treating it as a black box, and scores the reply. Assay never rebuilds your application's call internally — that copy would drift from the real thing the moment either side changed.

The call is configuration, not code. Describe your application's existing API once — from the UI's Settings page (`PATCH /settings/target`), or in `.env`:

```dotenv
ASSAY_TARGET_URL=https://my-app.example.com/api/chat
ASSAY_TARGET_HEADERS={"Authorization": "Bearer ${ASSAY_TARGET_API_KEY}"}
ASSAY_TARGET_BODY={"messages": [{"role": "user", "content": "{{input}}"}], "stream": false}
ASSAY_TARGET_OUTPUT_PATH=$.choices[0].message.content
```

Assay puts the test's `input` where `{{input}}` is, sends the request, and reads the answer at the output path. The same four settings cover very different applications:

| Application's API | `ASSAY_TARGET_BODY` | `ASSAY_TARGET_OUTPUT_PATH` |
|---|---|---|
| OpenAI-compatible chat | `{"messages": [{"role": "user", "content": "{{input}}"}]}` | `$.choices[0].message.content` |
| Custom Q&A | `{"question": "{{input}}", "lang": "en"}` | `$.answer` |
| Nested envelope | `{"payload": {"text": "{{input}}"}}` | `$.data.reply.text` |
| Assay's own contract (the defaults) | `{"input": "{{input}}"}` | `$.output` |

An application built for Assay only needs the URL; an existing one needs no change, only these settings. Secrets go in variables the headers reference (`${ASSAY_TARGET_API_KEY}`) — in the worker's environment or in `.env` — never in the header value itself, and are never logged or returned by the API; nor are the prompt or the answer — the worker logs status, latency and attempt counts only.

**Saved from the UI, or from the environment.** Settings saved through `PATCH /settings/target` apply as a whole and win over the `ASSAY_TARGET_*` variables until `DELETE /settings/target` resets them; `GET /settings/target` says which source is in effect. The worker reads them for every run that calls the application, so a change applies from the next run without a restart. To check a configuration before a run depends on it, `POST /settings/target/checks` asks a worker to call the application once — with the saved settings, or with proposed changes that aren't saved — and `GET /settings/target/checks/{check_id}` gives the answer or the exact reason it failed, as seen from the worker (so it also tells you whether a `${NAME}` in a header is set there). These endpoints have no authentication yet, which makes them unfit for production as they stand: see `docs/version_1/settings/dev_notes.md` note 9.

**What a run records.** Every run that evaluated anything shows, next to `results`, the exact answer it scored as `evaluated_output`, and `output_source` says where it came from: `recorded` (the test's own `model_output`) or `application` (obtained during the run). That's how two runs of the same test can honestly differ — your application answered differently — and a replay of an entry with no recorded answer asks the application again rather than reusing an old reply. The answer is never written back to the test or the entry.

**What counts as the answer.** A string at the output path is the answer as it is. A structured value — an object, as structured outputs produce (`{"output": {"category": "fraud"}}`), an array, a number — is the answer as JSON text, so the JSON checks can validate it; a path into it (`$.output.category`) reads just that field. The path `$` makes the **whole reply** the answer — the model's output together with the application's own fields (`model`, token counts, request ids) — which suits JSON checks on the reply's structure (e.g. JSON Field Equals on `$.model`), but not text checks like ROUGE or Exact Match, which would compare against the whole envelope; for those, point the path at the answer field.

**Checks can read other parts of the reply.** A run that called your application keeps its **whole reply** (`application_reply` on the run detail) — the model's output together with your application's own fields. Every check scores the answer at the output path by default, but an assigned check can set its own `answer_path` to read another part instead: e.g. ROUGE on the default `$.output`, and in the same test a Contains with `answer_path: "$.stop_reason"` and substring `end_turn`, checking the model finished normally (or a JSON Field Equals with `answer_path: "$"`, path `$.stop_reason` and value `"end_turn"`: JSON checks need JSON, so point them at an object, not at a single text field). Nothing at a check's path fails just that check. On a recorded answer, a check's `answer_path` reads inside it when it's JSON. Each result says which `answer_path` it read. A `null` or blank value is an **empty answer**: the application answered with nothing, as a model does when it refuses or is cut off, and the run scores it like any other answer — most checks fail — so it counts in the results.

**When the call fails.** A wrong configuration shows up on the first run, clearly: a non-2xx reply, nothing at the output path, a non-JSON reply, an unresolvable `${VAR}`, or no URL at all makes the run `NotRan` with the reason in `error`, and nothing is evaluated. Only failures that say "try again" are retried — connection errors, timeouts, 5xx and 429 (honouring `Retry-After`) — with `max_retries` extra attempts and exponential backoff; a 400 or 401 is final on the first attempt.

**Where it stops.** Single-turn HTTP with JSON in and out — the large majority — is pure configuration. Streaming-only endpoints (most accept `"stream": false`), multi-turn applications needing a session first, non-HTTP transports and multi-step auth are protocol-level exceptions, each a small adapter in code when one is actually needed, never one interface per application.

## Judging with an LLM

Five check types ask a judge model for a verdict instead of computing a score: **Correctness** and **Hallucination** compare the answer with the test's expected output; **Relevance**, **Bias** and **Toxicity** judge the answer on its own. Each type has a default rubric — what passes — and an assignment can set its own `rubric` to replace it.

**Choose the judge once, on the Settings page** (`PATCH /settings/judge`) or in `.env` (`ASSAY_JUDGE_*`): a provider (`anthropic` or `openai`), a model, and optionally a URL — the full endpoint, called exactly as written; leave it empty for the provider's own. Any OpenAI-compatible server works through `openai` with its chat completions endpoint (vLLM, Ollama, LM Studio, OpenRouter), and `GET /settings/judge` shows the resulting `endpoint`. The API key is never a setting: the worker reads it, when it calls, from the variable the settings name (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` by default), in its environment or in `.env`. `POST /settings/judge/checks` asks the judge one tiny question from a worker and says whether a readable verdict came back — or why not ("ANTHROPIC_API_KEY is not set on this server", "Judge answered HTTP 401 at https://api.anthropic.com/v1/messages: invalid x-api-key"; every failure names the URL called). Saved settings win over the environment as a whole until reset, and apply from the next run.

**What the judge sees, and what it returns.** The rubric, the test's input, the expected output (Correctness and Hallucination only) and the answer, each between tags it's told to treat as data — an answer under test can itself contain instructions — at temperature 0. It records a verdict, `passed` plus a two-to-four-sentence rationale in English: `passed` is the check's result, the rationale is its `detail` on a pass too, and `score` stays null — judge types have no threshold. The result also records the rubric it graded with, `rubric: {"text", "source"}` — `custom` (the assignment's own, which replaces the default outright) or `default`; the types that never see the expected output can't compare with it, whatever a custom rubric says. It records the judge that gave the verdict too, `judge: {"provider", "model"}`: the judge settings can change at any moment, so this is what says which model a stored verdict came from. Anthropic models answer through a forced tool call, OpenAI-compatible ones through a strict JSON schema; plain HTTP either way, no provider SDK.

**When the judge can't answer** — no judge configured, the key's variable unset, an HTTP error, retries exhausted (connection errors, timeouts, 5xx and 429 are retried), a refusal, or a reply that isn't a readable verdict — that check fails with the reason, the run's other checks are scored as usual, and the run is Amber or Red, not NotRan. **Cost:** each judge check is one paid call, a few seconds; each call logs its model, latency and tokens (never the prompt or the reply) under the run's ID.

## Running with statistics

One run says what happened once. A GenAI application answers differently each time, and a judge is itself a model: to claim "this check passes at least 90% of the time", run the same test, set or plan **N times as one batch** and let a statistical test decide — with a stated confidence, and an honest "not enough runs to say" when that's the truth. It's a third way to run, beside running once and replaying, and always a deliberate choice: every time is a full, paid run.

**Pick a question** from the catalogue (`GET /statistics/tests`): **Passes reliably** (the binomial gate: "does each check pass at least, say, 9 times in 10?"), **Scores high enough on average** (the one-sample t-test: "is each scored check's average on the right side of its threshold?"), or **The judge is consistent** (judge stability: "does each judge give the same verdict on the same recorded answer?"). Every name and sentence the API returns is in everyday words; the method behind each answer is in `method`, intervals and p-values, for whoever wants it. **See the cost first** (`POST /statistics/estimate`, which creates nothing): the floor — the fewest times that can conclude anything, e.g. 29 for "90% at 95% confidence", because only 29 straight passes are rare enough to rule out "90% or worse" — the sizes worth choosing, and the application and judge calls the batch will make. **Run it** (`POST /statistics/batches`): N standalone runs, or N live executions of a set or plan, executed by the workers like any runs and listed with them (`?batch=none` hides them). Watch progress and spend, and **stop** it if it's not worth finishing (`POST /statistics/batches/{id}/stop`).

**Plan before you pay.** A size is only worth its cost if it's likely to give an answer, and that depends on how often each check really passes — what Assay has already seen. So the estimate plans from each check's recent history: every size's chance that every check gets an answer (honest about how little a short history proves), the size worth paying for, each check's outlook (likely to pass, likely to fail, too close to call), the check that drives the cost, and what would make it cheaper — a check's own target, leaving a check out (its runs then skip it), or a little less certainty. A check that has never run gets a **trial** first ("Learn how it behaves": a few runs, no verdict, `Done`). And instead of guessing a size, run **until there's an answer**: set the most you'll pay for, and the batch runs in waves, stopping as soon as every check has its answer — each look calibrated exactly, so its claims are the same as a single test's. All three questions have this version; for an average, the first wave is the t-test's 10 runs.

**Read the result** once every run has finished (`GET /statistics/batches/{id}`): per entry and per check a verdict — `pass`, `fail`, or `inconclusive` with the size of a new batch that would likely decide it — the interval it was drawn from, the pass rate, the score distribution, and every run's outcome as a series to chart; for a set, whether failures concentrate in some entries. The batch's status rolls the verdicts up: **Passed** when every check is proven, **Failed** when one is proven to fail, **Inconclusive**, **Incomplete** when stopped, **NotRan** when nothing could be decided (every run Not Ran, or every check errored — no judge chosen, say: fix that, a bigger batch wouldn't help). A check that errored (a judge timeout) is counted apart, never as a failure. The result is computed once and stored: what was verified, with what, when.

**Did my change help?** Run a batch, change the application, run the same batch again, and compare them (`POST /statistics/comparisons`): pass rates A against B, **no worse than A** by a margin (the release-gate question), mean scores or score ranks for metrics, or **paired by entry** for a set — the strongest test of all, since each entry is compared with itself. The maths is plain Python, checked against scipy; the design and every decision are in `docs/version_1/statistics/`.

## Worker

`assay.worker` is a separate Celery process from the API — it promotes a `Pending` `TestRunModel` to `Running` and then a terminal outcome (`Green`/`Amber`/`Red`/`NotRan`; see the Runs section above for what those mean). `execute_run`, its one registered task, does exactly that — fetches the run, evaluates every assigned test type, rolls up the outcome, writes it back. Every run-creation endpoint in `services/runs/` now dispatches it: right after the creating transaction commits, `_dispatch_runs` (`services/runs/_common.py`) publishes an `execute_run` task per run by name (`send_task`, not `.delay()` — so the API never has to import the worker's task or evaluator modules just to publish a message). A dispatch failure is caught and logged rather than surfacing to the caller, so run creation itself never fails because the broker is unreachable — the run just stays `Pending`. What's still missing is on the worker side: each assigned type is routed to the evaluator engine its catalogue row names (`GET /tests/types`' `engine`), and every result records the `engine` and `engine_settings` it was scored with — the five deterministic engines (Exact Match, Contains, Regex Match, the JSON checks and the length limits) score for real, and so do the NLP metrics — ROUGE, BLEU, METEOR, BERTScore and Cosine Similarity (the `nlp` extra: `pip install -e ".[nlp]"`) — and so do the LLM judges (see [Judging with an LLM](#judging-with-an-llm)). BERTScore and Cosine Similarity run local models on the CPU (PyTorch; the `nlp` extra is about 1 GB installed): each model is downloaded from Hugging Face the first time a check uses it (about 800 MB for all three, into `~/.cache/huggingface`, or `HF_HOME`), loaded once per worker process on first use (about 0.5 GB of memory each), and shared by the process's threads — so for these metrics one worker process with more threads beats several processes. A model that isn't available fails just that check, with the reason. Their real-model tests are marked `slow` and excluded by default: `pytest -m slow` runs them. METEOR also needs NLTK's WordNet data (10 MB), downloaded once on each worker machine with `python -m nltk.downloader wordnet` (or `-d <dir>` plus `NLTK_DATA=<dir>` for a custom location); on python.org's macOS Python, run its "Install Certificates" script first or the download fails with `CERTIFICATE_VERIFY_FAILED`. A worker without it starts anyway, logs `METEOR checks will fail on this worker: …`, and fails just those checks; its scoring tests are skipped. The Regex Match engine needs the `regex` package (a core dependency, since the API also compiles each pattern with it when a check is assigned): unlike the stdlib `re`, it takes a per-match timeout, so a catastrophically backtracking pattern fails that one check instead of hanging a worker thread. `jsonschema`, which Matches JSON Schema scores with, is a core dependency for the same reason: the API checks a schema is valid when the check is assigned.

The worker process itself stays optional — the API runs fine, and every run-creation call still succeeds, whether or not a worker is actually running to pick anything up. `celery[redis]` is a base dependency of the API now (needed to publish), but talking to a real broker, running the worker process, and Postgres support for it (`psycopg2-binary`) are still behind the `worker` extra.

```bash
uv sync --extra worker
uv run celery -A assay.worker worker --loglevel=info
```

**Reconciliation scan (Celery Beat).** Direct dispatch covers the normal case; as a safety net for a run whose dispatch never reached the broker, a Beat process publishes a `reconcile_runs` task every `ASSAY_RECONCILIATION_INTERVAL_MINUTES` (default 60), which re-publishes `execute_run` for every `Pending` run older than `ASSAY_RECONCILIATION_PENDING_THRESHOLD_MINUTES` (default 15). Re-publishing a run that's actually still queued is harmless — the duplicate task's atomic claim matches nothing and no-ops. The same scan advances any batch running until there's an answer whose next wave was never released (a worker died between its last run and the `advance_batch` task that `execute_run` publishes when a wave finishes); advancing one that isn't stalled does nothing. Run exactly **one** Beat process per environment (on Azure, a single-replica container), alongside the workers:

```bash
uv run celery -A assay.worker beat --loglevel=info
```

`assay/worker/db.py`'s sync engine pool (`ASSAY_WORKER_DB_POOL_SIZE`/`ASSAY_WORKER_DB_MAX_OVERFLOW`, both `10` by default) needs sizing against whichever `--pool` the worker actually runs under: under the default `--pool=prefork`, each forked child process gets its own copy of that pool, so the per-process number needs to stay small; under `--pool=threads` (or `--pool=solo`), there's no forking at all — one process, one shared pool across every concurrent thread — so it needs to cover total `--concurrency` instead. `--pool=threads` is also the workaround for a real, known Celery/billiard bug (`ValueError: not enough values to unpack (expected 3, got 0)` in `fast_trace_task`) that surfaces whenever a worker child process starts without inheriting the parent's fork-time initialization — most commonly hit on platforms/Python versions where `prefork`'s usual reliance on `fork()` doesn't hold ([celery#4178](https://github.com/celery/celery/issues/4178) and similar).

Broker and result backend are controlled by `ASSAY_CELERY_BROKER_URL`/`ASSAY_CELERY_RESULT_BACKEND` — same "no code changes, only the URL changes" story as the database. Three environments, in order of how close each gets to what production actually runs:

| Environment | Broker URL | Result backend URL |
|-------------|-----------|---------------------|
| Local (default) | `redis://localhost:6379/0` | `redis://localhost:6379/0` |
| Local, nothing to install | `sqla+sqlite:///./celery_broker.sqlite` | `db+sqlite:///./celery_results.sqlite` |
| Production (Azure Cache for Redis, or any TLS-only managed Redis) | `rediss://:<access-key>@<name>.redis.cache.windows.net:6380/0` | same, `rediss://...` |

**Local default — real Redis.** `brew install redis && brew services start redis` on macOS (or `docker run -p 6379:6379 redis`) — free for local dev, no license or cost concern (Redis 8+ is AGPLv3). This runs the same broker technology production uses, not a stand-in for it.

**Local, no separate service.** If you'd rather not install Redis, swap both URLs to the SQLite-backed alternative — Kombu's SQLAlchemy transport (broker) and Celery's own SQLAlchemy result backend, both files created automatically next to the project root, gitignored the same way `assay.db` is. Still a real separate worker process with a real queue, not `task_always_eager` (which skips the worker entirely and runs tasks synchronously in-process) — it just trades "same tech as prod" for "nothing to install."

**Production / Azure Cache for Redis.** Note the scheme and port: `rediss://`, not `redis://` — Azure Cache for Redis requires TLS by default — and port `6380`, not `6379`. The `rediss://` scheme alone is enough; `assay.worker.celery_app` detects it and turns on certificate verification (`ssl_cert_reqs=CERT_REQUIRED`) automatically — Kombu's own default for `rediss://` is `CERT_NONE` (TLS with no verification at all), which this deliberately overrides. A plain `redis://` URL is left untouched.

**Note on hosting.** An Azure Web App is a good fit for the API (it's a standard containerized HTTP service — this repo already has a `Dockerfile`), but not for the worker: Web Apps are built around serving HTTP traffic, and a Celery worker just polls its broker forever without binding a port. [Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/overview) (no ingress) or a continuous [WebJob](https://learn.microsoft.com/en-us/azure/app-service/webjobs-create) are the more natural fit for a long-running background process like this one.

### Worker image

`Dockerfile.worker` builds the worker's own image, separate from the API's `Dockerfile`: Python 3.11, the `worker` and `nlp` extras (never `dev`), and NLTK's WordNet data downloaded at build time, so METEOR works from the first run with no download at run time. It runs as a non-root user and logs JSON by default (`ASSAY_LOG_FORMAT`, overridable). Beat uses the same image with another command; run exactly one per environment.

```bash
docker build -f Dockerfile.worker -t assay-worker .
docker run --env-file .env assay-worker                                         # the worker
docker run --env-file .env assay-worker celery -A assay.worker beat --loglevel=info  # Beat
```

The container needs the same settings as the API — at least `ASSAY_DATABASE_URL` and the Celery broker/result URLs, pointing at services it can reach (not `localhost`, which inside a container is the container itself).

### Task history — Flower

The app's own audit trail (`GET /runs` and friends — see [Runs](#runs)) is the real history of what happened to a *run*: it persists in the database regardless of whether Celery or Redis are even still running, and it's what a client should actually read. [Flower](https://flower.readthedocs.io/) is a separate, worker-level view on top of that — a web dashboard over the queue itself: every task's args, state, runtime, which worker handled it, retries, and the raw traceback if the task process itself failed (distinct from a run merely scoring badly). Useful for debugging the worker, not a replacement for the app's own history.

Included in the `dev` extra:

```bash
uv sync --extra dev --extra worker
uv run celery -A assay.worker flower --port=5555
```

Then open <http://localhost:5555>. By default Flower only keeps history in memory for as long as it's running — add `--persistent=True --db=flower.db` to keep it across restarts.

`execute_run` is the one task registered, and every run-creation endpoint now dispatches it — Flower's task list fills in as soon as a worker is actually running to receive what the API publishes.

## Observability

### Metrics

Assay exposes a Prometheus-compatible scrape endpoint at `GET /metrics`. It is not listed in the OpenAPI docs (`/docs`) because it returns plain text rather than JSON, but it is active on every running instance.

```bash
curl http://127.0.0.1:8000/metrics
```

The endpoint provides request count and latency histograms (`http_requests_total`, `http_request_duration_seconds`) labelled by method, status code, and handler. Point any Prometheus scraper — or a compatible platform like Datadog, Grafana Cloud, or Google Cloud Managed Prometheus — at this URL.

### Logging

The API logs to stdout, one record per line, and lets the platform collect it from there — no log files. `ASSAY_LOG_FORMAT` picks the shape: `text` for a terminal (the default), `json` for a log collector, where every field below becomes a key. `ASSAY_LOG_LEVEL` sets the level for the app's own loggers (`assay.*`); third-party loggers stay at `INFO` whatever it's set to, and `DEBUG` additionally logs every SQL statement (`sqlalchemy.engine`). uvicorn's own output (startup, shutdown, the traceback of any unhandled exception) is routed through the same formatter, so a production log is uniform.

```
2026-09-26 16:47:18,668 WARNING [fe-abc.123] assay.access: GET /test-sets/00000000-… -> 404 (9.4 ms) error="Test set with ID '00000000-…' not found"
{"timestamp": "2026-09-26T14:47:19.062+00:00", "level": "WARNING", "logger": "assay.access", "message": "GET /test-sets/00000000-… -> 404 (9.5 ms) error=\"…\"", "request_id": "fe-abc.123", "method": "GET", "path": "/test-sets/00000000-…", "status_code": 404, "duration_ms": 9.5, "route": "/test-sets/{test_set_id}", "client_ip": "10.0.0.7", "error": "Test set with ID '00000000-…' not found"}
```

**Request IDs.** Every request gets an ID, and every log record written while handling it carries that ID (`[fe-abc.123]` above; `-` outside a request). Send an `X-Request-ID` header to have your own ID reused — handy for correlating a client's logs with the server's — otherwise one is generated; either way it comes back in the response's `X-Request-ID` header (readable cross-origin, it's CORS-exposed). A supplied ID is only honoured if it's 1–128 characters of `A-Z a-z 0-9 . _ : -`; anything else is replaced.

**What one request produces.** Exactly one access line on the `assay.access` logger once the response is done — `INFO` for a success, `WARNING` for a 4xx, `ERROR` for a 5xx, `DEBUG` for the constantly-polled `/health` and `/metrics` — with `method`, `path`, `route` (the template, for aggregating), `status_code`, `duration_ms` and `client_ip`. When the request failed, that same line carries the reason as `error`: the response's `detail` for a 4xx (validation errors are summarised as `location: message`, never echoing the submitted value), the exception for a 500. Plus one `INFO` event from the service layer for every state change (a dataset created with its row count, a test set renamed from/to, an execution created with its run count, runs dispatched, ...) — always IDs, names and counts, never a test's input/output or a dataset row.

**Unhandled errors.** A 500 responds with `{"detail": "Internal server error", "request_id": "<id>"}` and the `X-Request-ID` header — quote the ID when reporting it, it's the key to the access line and the traceback (logged once, by the server, with the same ID). Exceptions the API raises on purpose (404/409/422) keep FastAPI's usual `{"detail": ...}` body.

**The worker and Beat log the same way.** `assay.worker` hooks Celery's `setup_logging` signal and applies the same configuration, so `ASSAY_LOG_LEVEL`/`ASSAY_LOG_FORMAT` apply there too and the output has the same shape (Celery's own `--loglevel` keeps governing Celery's own loggers — task received/succeeded, Beat ticks). The API puts each request's ID into the task message, so a run's worker lines carry the request ID of the call that created it (a run re-published by the reconciliation scan shows `-`), and every line a run produces also carries `run_id` (a JSON field). Per run: `Executing run … (test set entry, 2 test types)`, then `Run … finished: Amber (1/2 passed) in 84.2 ms`; a duplicate delivery logs `Run … not claimed`; a run that couldn't be attempted logs the traceback at `ERROR` and lands on `NotRan`; an evaluator that raises logs a `WARNING` with its traceback and the run carries on. The worker and Beat each announce themselves at startup (pool, concurrency and the broker with its password masked; every scheduled task and its interval).

## Tests

```bash
uv run pytest
```

### Skipped and deselected tests

A plain `uv run pytest` reports some tests as skipped and some as deselected. That's by design: these tests need something the `dev` extra doesn't install, so they step aside instead of failing.

| Tests | Reported as | Why | To run them |
|---|---|---|---|
| METEOR's scoring tests (`tests/worker/evaluators/test_meteor.py`) | skipped | They need NLTK's WordNet data, a separate download | `uv run python -m nltk.downloader wordnet`, then `uv run pytest` |
| BERTScore's scoring tests (`tests/worker/evaluators/test_bertscore.py`) | skipped | The scoring arithmetic runs on PyTorch, which only the `nlp` extra installs | `uv sync --extra dev --extra nlp`, then `uv run pytest` |
| BERTScore and Cosine Similarity on the real models (`tests/worker/evaluators/test_embedding_engines_slow.py`) | deselected | Marked `slow`: they need the `nlp` extra and download about 800 MB of models the first time | `uv run pytest -m slow` |

Each skip names its reason; `uv run pytest -rs` lists them. Everything else in these files runs without the extra dependencies.

### Coverage

Run with terminal coverage report (shows which lines were not hit):

```bash
uv run pytest --cov=assay --cov-report=term-missing
```

Run with HTML report (open `htmlcov/index.html` in a browser for line-by-line highlights):

```bash
uv run pytest --cov=assay --cov-report=term-missing --cov-report=html
```

To enforce a minimum coverage threshold and fail the pipeline if it drops below it, add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "--cov=assay --cov-report=term-missing --cov-fail-under=80"
```

With this in place, `uv run pytest` will fail with a non-zero exit code if coverage falls below 80% — CI pipelines treat a non-zero exit as a build failure automatically.

## TODOs

- Test plans — triggering a live execution (`POST /runs/test-plans/{test_plan_id}`) and replaying a specific past plan execution (`POST /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}`) are both now implemented. Create, list, get, rename, delete, listing a plan's test sets, linking test sets to a plan, and unlinking test sets from a plan are all implemented too.
- Test runs — creation is fully implemented for every mode: standalone (`POST /runs/standalone/{test_id}`), live and replay for test sets (`POST /runs/test-sets/{test_set_id}`, `POST /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}`), and live and replay for test plans (`POST /runs/test-plans/{test_plan_id}`, `POST /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}`). A standalone run's own state can now be read back too (`GET /runs/standalone/{test_id}/test-runs`, `GET /runs/standalone/{test_id}/test-runs/{test_run_id}`), and so can a test set's and a test plan's past executions (`GET /runs/test-sets/{test_set_id}/executions`, `GET /runs/test-plans/{test_plan_id}/executions`), each with its `run_count` and `replayed_execution_id`. The audit trail goes further now: the runs a specific execution produced can be listed for both test sets and test plans (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs`, `GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs`), and a single run's full detail — including the frozen entry it ran against — can now be read back for both test sets (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}`) and test plans (`GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}`). The audit trail for both test sets and test plans is now complete end to end. A system-wide, origin-agnostic view now exists too: `GET /runs` paginates every run ever created regardless of how it was triggered — standalone, test-set-triggered, or test-plan-triggered alike — so a caller doesn't have to loop over every test, test set, and test plan individually to see what's running or recently ran. `GET /runs/executions` does the same for executions specifically — test set and test plan executions only, since a standalone run has no execution wrapper to aggregate — so a caller doesn't have to fan out one `GET /runs/{test-sets,test-plans}/{id}/executions` call per test set and test plan either. Every run-creation endpoint now dispatches its run(s) to `assay.worker` right after creation (see [Worker](#worker)), and `execute_run` does promote a run all the way to a terminal outcome (`Green`/`Amber`/`Red`/`NotRan`) when it runs — routing each assigned type to the engine its catalogue row names and recording that engine on the result — the deterministic engines (Exact Match, Contains, Regex Match) score for real; so do every NLP metric (ROUGE, BLEU, METEOR, BERTScore, Cosine Similarity) and the LLM judges (Correctness, Relevance, Bias, Toxicity, Hallucination), with a judge model chosen under Settings. A test with no recorded `model_output` no longer sits idle: the run asks the application under test for the answer (see [Testing your application](#testing-your-application)) and every run records what it scored as `evaluated_output` with its `output_source`.
- Statistics: comparing several batches at once ("which version passes most"), deferred in `docs/version_1/statistics/dev_notes.md` note 24.
