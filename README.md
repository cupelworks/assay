# Assay

Test how a GenAI-powered application behaves — and keep a reproducible record of it. Bring the prompts you send your application, the outputs it produced and the outputs you expected; Assay scores each case with deterministic checks, NLP metrics against a threshold, and LLM-as-judge verdicts against a rubric, organises cases into stable test sets and plans, and records every run's outcome.

## Status

Active development. Dataset CRUD operations, the z-test, test case management (create, list, get, update, delete), the test set layer (create, list, get, rename, delete), and test set entries (snapshot tests into a set, list, get, update — until the entry has been run, delete individual entries or the whole set, or unlink individual entries from the set without deleting them) are implemented. Deleting a test set cascades to its entries, but only while none of them have runs; bulk-deleting individual entries enforces the same guard and is all-or-nothing. Unlinking entries has no such guard — it's the operation for detaching a run-having entry from a set's membership without touching its frozen content or execution history. Test plans are mostly implemented (create, list, get, rename, delete, list the test sets included in a plan, link test sets to a plan, and unlink test sets from a plan — unlinking is always allowed, even if the test set already has runs recorded via this plan). Deleting a plan cascades to its own links to test sets (never blocked, mirroring the unlink behavior), but is permanently blocked once the plan has ever been executed, so a run's audit trail can never lose track of which campaign produced it. The schema-level groundwork for grouping runs by execution — live fan-out vs. replaying a specific past execution — is in place for both test plans and standalone test sets, and is now fully wired up end-to-end for both test sets and test plans (see below). Standalone runs can now be created (`POST /runs/standalone/{test_id}`) — exactly one `TestRunModel` row per call, in `Pending` status, blocked with a 409 if the test has no test types assigned. Live test-set execution can now be triggered too (`POST /runs/test-sets/{test_set_id}`) — one `TestSetExecutionModel` plus one `Pending` `TestRunModel` per entry currently in the set, blocked with a 409 if the set has no entries or if any entry has no test types assigned. A past test-set execution can now be replayed as well (`POST /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}`) — a new `TestSetExecutionModel` plus one `Pending` `TestRunModel` per entry the replayed execution ran, targeting the exact same frozen entries regardless of the set's current membership; blocked with a 404 if the execution doesn't exist or isn't linked to this test set, or a 409 if it has zero runs to replay. Live test-plan execution can now be triggered too (`POST /runs/test-plans/{test_plan_id}`) — one `TestPlanExecutionModel` plus one `Pending` `TestRunModel` per entry across every test set currently linked to the plan, blocked with a 409 if the plan has no linked test sets, if any linked test set has no entries, or if any entry has no test types assigned. A past test-plan execution can now be replayed as well (`POST /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}`) — a new `TestPlanExecutionModel` plus one `Pending` `TestRunModel` per entry the replayed execution ran, targeting the exact same frozen entries regardless of the plan's current linked test sets; blocked with a 404 if the execution doesn't exist or isn't linked to this test plan, or a 409 if it has zero runs to replay. A standalone run's own state can now be read back too: `GET /runs/standalone/{test_id}/test-runs` paginates every standalone run created for a test, and `GET /runs/standalone/{test_id}/test-runs/{test_run_id}` returns one run's full detail, including `results`/`error`/`executed_at` once populated and the frozen copy of the test the run was created from — every run, standalone included, is evaluated against a copy that later edits to the test never change. There's also a system-wide view spanning every origin: `GET /runs` paginates every run ever created — standalone, test-set-triggered, and test-plan-triggered alike, newest first — with each item's `origin` field (`Standalone`, `TestSet`, or `TestPlan`) determining which of `test_case_id`, or the `test_set_entry_id` + `test_set_execution_id`/`test_plan_execution_id` pair, is populated. `GET /runs/executions` is the equivalent system-wide view for executions rather than runs — every `TestSetExecutionModel`/`TestPlanExecutionModel` ever triggered, across every test set and test plan, newest first — but deliberately excludes standalone runs, since a standalone run has no execution wrapper to aggregate in the first place. A test set's and a test plan's past executions can now be listed as well: `GET /runs/test-sets/{test_set_id}/executions` and `GET /runs/test-plans/{test_plan_id}/executions` each paginate every execution (live or replayed) ever triggered for the set/plan, with a `run_count` rolling up how many `TestRunModel` rows it produced. The runs a specific execution produced can now be listed too, for both test sets (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs`) and test plans (`GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs`), and now a specific run's full detail can be read back for both test sets (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}`) and test plans (`GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}`), including the frozen test set entry it ran against in both cases. The test-plan version returns a nullable `test_set_id` — the entry's current test set, `null` once it's been unlinked — rather than a path parameter like the test-set version, since one plan execution spans every test set linked to the plan; both versions stay reachable even after the entry is unlinked from its set, and the test-plan one also stays reachable if that set is later unlinked from the plan, since plan-to-set links never freeze. Every run-creation endpoint now dispatches its run(s) to `assay.worker` right after creation (see [Worker](#worker)), and `execute_run` does promote a run all the way to a terminal outcome (`Green`/`Amber`/`Red`/`NotRan`) when it runs — routing each assigned type to the engine its catalogue row names and recording that engine on the result — the deterministic engines (Exact Match, Contains, Regex Match) score for real; so do ROUGE, BLEU and METEOR; the other NLP-metric engines and the LLM-judge engine are still stubs. A test with no recorded `model_output` no longer sits idle: the run asks the application under test for the answer (see [Testing your application](#testing-your-application)) and every run records what it scored as `evaluated_output` with its `output_source`. The groundwork for real evaluators has started: every test type in the catalogue (`GET /tests/types`) now declares, as data, which evaluator engine scores it (`engine`), that engine's settings for the type (`engine_settings`), and how a threshold-scored type passes (`comparison`), with each `threshold` bounded on the metric's own native scale (BLEU 0–100, Cosine Similarity −1 to 1, the rest 0–1) — see `docs/evaluators/`. The application under test can also be configured from the UI: its settings are saved in the database (`GET`/`PATCH`/`DELETE /settings/target`, falling back to the `ASSAY_TARGET_*` environment when nothing is saved), and a check (`POST /settings/target/checks`) asks a worker to call it once and reports the answer or the reason it failed — see `docs/settings/`. LLM-as-judge evaluators and the broader statistics engine are not yet built either.

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
| `ASSAY_LLM_PROVIDER` | *(unset)* | Reserved for the LLM-as-judge evaluator's provider selection (`anthropic` or `openai`). Not yet read anywhere — `Settings` in `config.py` has no field for it yet, since the evaluator itself isn't implemented |
| `ASSAY_LLM_MODEL` | *(unset)* | Reserved for the LLM-as-judge evaluator's model selection. Same caveat as `ASSAY_LLM_PROVIDER` |
| `ANTHROPIC_API_KEY` | *(unset)* | Will be needed once the Anthropic LLM-as-judge evaluator ships. Install the optional extra ahead of time with `pip install -e ".[anthropic]"` (or `uv sync --extra anthropic`) |
| `OPENAI_API_KEY` | *(unset)* | Will be needed once the OpenAI LLM-as-judge evaluator ships. Install with the `openai` extra, same pattern as above |
| `ASSAY_TARGET_URL` | *(unset)* | The application under test — used only when a test has no recorded `model_output`: the run asks the application for the answer and scores that. Unset means no application is configured; such a run lands on `NotRan` saying so. All `ASSAY_TARGET_*` variables are the **fallback**: once the settings are saved from the UI (`PATCH /settings/target`), the saved ones apply as a whole until reset. The same rules apply to both, checked at start-up here — see [Testing your application](#testing-your-application) |
| `ASSAY_TARGET_METHOD` | `POST` | HTTP method for that call: `POST`, `PUT` or `PATCH` |
| `ASSAY_TARGET_HEADERS` | `{}` | JSON object of headers; values may reference `${ENV_VAR}`, resolved from the environment at call time so a token never sits in `.env` |
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
├── db.py                # Async SQLAlchemy engine, session dependency, SQLite FK pragma
├── logging_config.py    # configure_logging(): stdout, text/json, request ID on every record — see Logging below
├── middleware.py        # RequestContextMiddleware: X-Request-ID in/out, one access line per request
├── exception_handlers.py # JSON 500 with request_id; 4xx/422 keep FastAPI's responses, feed the access line
├── api/                 # HTTP routes — one file per domain
│   ├── datasets.py      # Dataset endpoints
│   ├── test.py          # Test case endpoints
│   ├── test_sets.py     # Test set and test set entry endpoints
│   ├── test_plan.py     # Test plan endpoints
│   ├── runs/            # Run creation and reads, split by sub-domain (too much for one file)
│   │   ├── __init__.py       # Combines the four sub-routers below into one `router`
│   │   ├── all.py            # System-wide listings: every run across every origin (standalone, test-set-triggered, test-plan-triggered), and every execution across every test set and test plan
│   │   ├── standalone.py     # Standalone run creation, listing runs for a test, and reading a single run's details back
│   │   ├── test_sets.py      # Live/replay test-set run creation, listing a test set's past executions, listing the runs a specific execution produced, and reading one run's full detail back
│   │   └── test_plans.py     # Live/replay test-plan run creation, listing a test plan's past executions, and listing the runs a specific execution produced
│   ├── settings.py      # Settings saved from the UI: the application under test, and checks of it
│   ├── stats.py         # Statistical test endpoints
│   └── meta.py          # Health check
├── schemas/             # Pydantic models — API validation and serialization
│   ├── __init__.py      # Re-exports all public models
│   ├── _common.py       # Shared base models (Pagination)
│   ├── datasets.py      # Dataset and row schemas
│   ├── test_sets.py     # Test set schemas (TestSetMetadata, PaginatedTestSetMetadataResponse, etc.)
│   ├── test_set_entries.py  # Test set entry schemas (TestSetEntryDetails, PaginatedTestSetEntriesDetails, etc.)
│   ├── test_plans.py    # Test plan schemas (TestPlanMetadata, PaginatedTestPlanMetadataResponse, etc.)
│   ├── test_plan_entries.py  # Test plan entry schemas (TestPlanEntryDetails, PaginatedTestPlanEntriesDetails)
│   ├── tests.py         # Test case schemas (CreateTestCaseRequest, ModifyTestCaseRequest, etc.)
│   ├── runs.py          # Run schemas (RunID, RunStatus, RunCreationDate, RunScores, RunError, RunExecutionDate, StandaloneRunCreationMetadata, StandaloneRunDetails, PaginatedStandaloneRunCreationMetadata, TestSetExecutionID, TestSetLiveRunCreationMetadata, TestSetReplayedExecutionID, TestSetReplayedExecutionCreationMetadata, TestSetExecutionMetadata, PaginatedTestSetExecutionMetadata, TestSetExecutionRunMetadata, PaginatedTestSetExecutionRunMetadata, TestSetExecutionRunDetails, TestPlanExecutionID, TestPlanExecutionCreationDate, TestPlanLiveRunCreationMetadata, TestPlanReplayedExecutionID, TestPlanReplayedExecutionCreationMetadata, TestPlanExecutionMetadata, PaginatedTestPlanExecutionMetadata, TestPlanExecutionRunMetadata, PaginatedTestPlanExecutionRunMetadata, RunOrigin, RunMetadata, PaginatedRunMetadata, ExecutionOrigin, ExecutionMetadata, PaginatedExecutionMetadata)
│   ├── settings.py      # TargetSettings (the one definition of the application-under-test settings and their rules), TargetSettingsUpdate, TargetSettingsRead, TargetCheckRequest, TargetCheck
│   └── stats.py         # ZTestRequest, ZTestResult
├── services/            # Business logic — one file per operation
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
│   │   ├── get_run_metadata.py         # Paginated listing of every standalone run created for a test, every execution triggered for a test set or test plan, or every run a specific test-set/test-plan execution produced
│   │   └── get_run_details.py          # Full detail for a single standalone run or a single test-set-execution run, including results/error/executed_at once populated
│   ├── settings/
│   │   ├── _common.py                  # Shared helpers (_find_target_row, _apply_or_422 — the fields sent applied to the settings in effect, validated as a whole)
│   │   ├── get_target_settings.py      # The settings in effect and their source (database or environment)
│   │   ├── update_target_settings.py   # PATCH: apply, validate, save the complete row
│   │   ├── reset_target_settings.py    # DELETE: drop the row, the environment applies again
│   │   ├── create_target_check.py      # Store a check with the settings to check, publish check_target by name
│   │   └── get_target_check.py         # A check as far as it has got
│   └── stats.py         # run_z_test
├── models/              # SQLAlchemy ORM models — database table definitions
│   ├── __init__.py
│   ├── base.py          # Shared DeclarativeBase
│   ├── datasets.py      # DatasetModel, DatasetRowModel
│   ├── settings.py      # SettingsModel (one JSON row per group of settings), TargetCheckModel
│   ├── stats.py         # StatisticalVerificationModel
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

### Health
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{"status": "ok"}` |

### Datasets
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/datasets` | List all datasets (paginated) |
| `GET` | `/datasets/{dataset_id}` | Retrieve metadata for a single dataset |
| `GET` | `/datasets/{dataset_id}/rows` | List rows in a dataset (paginated) |
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
| `GET` | `/tests` | List all test cases (paginated, includes total count) |
| `GET` | `/tests/{test_case_id}` | Retrieve a single test case by ID |
| `POST` | `/tests` | Create a single test case manually |
| `POST` | `/tests/from-dataset` | Bulk-create test cases from all rows in a dataset |
| `PATCH` | `/tests/{test_case_id}` | Partially update a test case — only sent fields are changed; `expected_output`/`model_output` sent as `null` are cleared, `name`/`input` sent as `null` are left as they are; unknown fields are rejected |
| `DELETE` | `/tests` | Delete test cases by ID (guards against linked test sets and test runs) |

### Test sets
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/test-sets` | List all test sets (paginated) |
| `GET` | `/test-sets/{test_set_id}` | Retrieve metadata for a single test set |
| `POST` | `/test-sets` | Create a new test set (name must be unique) |
| `PATCH` | `/test-sets/{test_set_id}` | Rename a test set — resubmitting its current, unchanged name is a no-op, not a 409 |
| `DELETE` | `/test-sets/{test_set_id}` | Delete a test set and all of its entries — blocked with a 409 if any entry has runs |
| `GET` | `/test-sets/{test_set_id}/entries` | List all entries (snapshotted tests) in a test set (paginated) |
| `GET` | `/test-sets/{test_set_id}/entries/{entry_id}` | Retrieve a single entry by ID |
| `POST` | `/test-sets/{test_set_id}/entries` | Snapshot one or more tests into a test set as entries |
| `PATCH` | `/test-sets/{test_set_id}/entries/{entry_id}` | Partially update an entry, with the same `null` rules as a test case — only allowed until it has been run at least once (409 otherwise) |
| `DELETE` | `/test-sets/{test_set_id}/entries` | Bulk-delete one or more entries by ID — all-or-nothing, blocked with a 409 if any target entry has runs |
| `PATCH` | `/test-sets/{test_set_id}/entries` | Bulk-unlink one or more entries by ID (clears `test_set_id`, entry row and any runs left untouched) — all-or-nothing, no runs guard, unlike the sibling `DELETE` |

### Test plans
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/test-plans` | List all test plans (paginated) |
| `GET` | `/test-plans/{test_plan_id}` | Retrieve metadata for a single test plan |
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
| `GET` | `/runs` | Paginated list of every run in the system, regardless of origin (standalone, test-set-triggered, or test-plan-triggered) — `id`, `status`, `created_at`, `origin`, plus the origin-specific ID(s) (`test_case_id`, or `test_set_entry_id` + `test_set_execution_id` + `test_set_id`, or `test_set_entry_id` + `test_plan_execution_id` + `test_plan_id`) per item, newest first. `test_set_id`/`test_plan_id` let a caller deep-link a row without a separate lookup. Optional `?status=` filters to one status (`Pending`/`Running`/`Green`/`Amber`/`Red`/`NotRan`); optional `?origin=` filters to one origin (`Standalone`/`TestSet`/`TestPlan`); both combine |
| `GET` | `/runs/executions` | Paginated list of every execution across every test set and test plan (live or replayed) — `id`, `created_at`, `origin` (`TestSet`/`TestPlan`), `run_count`, plus the origin-specific ID(s) (`test_set_id`/`test_plan_id`, and `replayed_test_set_execution_id`/`replayed_test_plan_execution_id` if it was a replay) per item, newest first |
| `GET` | `/runs/standalone/{test_id}/test-runs` | Paginated list of every standalone run ever created for a test — `id`, `status`, `created_at`, `test_case_id` per item, newest first |
| `GET` | `/runs/standalone/{test_id}/test-runs/{test_run_id}` | Full detail for a single standalone run — `id`, `status`, `created_at`, `test_case_id`, plus `results`/`error`/`executed_at`, which stay null until the run reaches a terminal status, plus the run's frozen copy of the test (`name`, `input`, `expected_output`, `model_output`, `test_type_assignments`, `test_case_snapshot_at`) |
| `GET` | `/runs/test-sets/{test_set_id}/executions` | Paginated list of every execution (live or replayed) ever triggered for a test set — `id`, `created_at`, `test_set_id`, `run_count`, `replayed_execution_id` per item, newest first |
| `GET` | `/runs/test-plans/{test_plan_id}/executions` | Paginated list of every execution (live or replayed) ever triggered for a test plan — `id`, `created_at`, `test_plan_id`, `run_count`, `replayed_execution_id` per item, newest first |
| `GET` | `/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs` | Paginated list of every run a specific test set execution produced — `id`, `status`, `created_at`, `test_set_entry_id`, `test_set_execution_id` per item, newest first. 404 if the test set or execution doesn't exist, or the execution isn't linked to this test set |
| `GET` | `/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}` | Full detail for a single run produced by a test set execution, including `results`/`error`/`executed_at` and the frozen test set entry it ran against (`test_case_id`, `name`, `input`, `expected_output`, `model_output`, `test_type_assignments`, `test_case_snapshot_at`). 404 if the test set, execution, or run doesn't exist, or if any of them aren't linked to the one above it in the path |
| `GET` | `/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs` | Paginated list of every run a specific test plan execution produced — `id`, `status`, `created_at`, `test_set_entry_id`, `test_plan_execution_id` per item, newest first. 404 if the test plan or execution doesn't exist, or the execution isn't linked to this test plan |
| `GET` | `/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}` | Full detail for a single run produced by a test plan execution, including `results`/`error`/`executed_at` and the frozen test set entry it ran against (`test_case_id`, `name`, `input`, `expected_output`, `model_output`, `test_type_assignments`, `test_case_snapshot_at`). Also returns `test_plan_id` and a nullable `test_set_id` — the entry's *current* test set, `null` if it's since been unlinked; unlike the test-set version, there's no `test_set_id` in the path, since one plan execution spans every test set linked to the plan. 404 if the test plan, execution, or run doesn't exist, or if any of them aren't linked to the one above it in the path |

### Settings
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/settings/target` | The application-under-test settings in effect (`url`, `method`, `headers`, `body`, `output_path`, `timeout_seconds`, `max_retries`), plus `source` — `database` (saved from the UI) or `environment` (the `ASSAY_TARGET_*` fallback) — and `updated_at`. Header values come back as stored: a `${NAME}` reference is never expanded |
| `PATCH` | `/settings/target` | Change some settings; only the fields sent change (`url: null` unsets it), `headers`/`body` replaced whole. The result is validated as a whole — 422 listing every problem, nothing saved — then saved as the complete group; the first save carries the environment's settings forward. Applies from the next run, no worker restart |
| `DELETE` | `/settings/target` | Reset: drop the saved settings so the environment's apply again; returns them. Not an error when nothing is saved |
| `POST` | `/settings/target/checks` | Ask a worker to call the application once with `input`, using the settings in effect plus any proposed `settings` (validated like a PATCH, never saved). 202 with the check, `pending`; `completed` at once with `ok: false` if it couldn't be sent to a worker. 422 for invalid proposed settings |
| `GET` | `/settings/target/checks/{check_id}` | A check as far as it has got: `pending`, `running`, or `completed` with `ok`, `status_code`, `latency_ms`, and the `answer` or the `error` (the same reason a run would get). A check queued over 5 minutes completes as expired without calling the application. 404 for an unknown ID |

### Statistical tests
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/statistical-tests/z-test` | One-sample z-test for metric score distributions |

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

An application built for Assay only needs the URL; an existing one needs no change, only these settings. Secrets go in environment variables of the worker that the headers reference (`${ASSAY_TARGET_API_KEY}`), never in the header value itself, and are never logged or returned by the API; nor are the prompt or the answer — the worker logs status, latency and attempt counts only.

**Saved from the UI, or from the environment.** Settings saved through `PATCH /settings/target` apply as a whole and win over the `ASSAY_TARGET_*` variables until `DELETE /settings/target` resets them; `GET /settings/target` says which source is in effect. The worker reads them for every run that calls the application, so a change applies from the next run without a restart. To check a configuration before a run depends on it, `POST /settings/target/checks` asks a worker to call the application once — with the saved settings, or with proposed changes that aren't saved — and `GET /settings/target/checks/{check_id}` gives the answer or the exact reason it failed, as seen from the worker (so it also tells you whether a `${NAME}` in a header is set there). These endpoints have no authentication yet, which makes them unfit for production as they stand: see `docs/settings/dev_notes.md` note 9.

**What a run records.** Every run that evaluated anything shows, next to `results`, the exact answer it scored as `evaluated_output`, and `output_source` says where it came from: `recorded` (the test's own `model_output`) or `application` (obtained during the run). That's how two runs of the same test can honestly differ — your application answered differently — and a replay of an entry with no recorded answer asks the application again rather than reusing an old reply. The answer is never written back to the test or the entry.

**What counts as the answer.** A string at the output path is the answer as it is. A structured value — an object, as structured outputs produce (`{"output": {"category": "fraud"}}`), an array, a number — is the answer as JSON text, so the JSON checks can validate it; a path into it (`$.output.category`) reads just that field. The path `$` makes the **whole reply** the answer — the model's output together with the application's own fields (`model`, token counts, request ids) — which suits JSON checks on the reply's structure (e.g. JSON Field Equals on `$.model`), but not text checks like ROUGE or Exact Match, which would compare against the whole envelope; for those, point the path at the answer field.

**Checks can read other parts of the reply.** A run that called your application keeps its **whole reply** (`application_reply` on the run detail) — the model's output together with your application's own fields. Every check scores the answer at the output path by default, but an assigned check can set its own `answer_path` to read another part instead: e.g. ROUGE on the default `$.output`, and in the same test a Contains with `answer_path: "$.stop_reason"` and substring `end_turn`, checking the model finished normally (or a JSON Field Equals with `answer_path: "$"`, path `$.stop_reason` and value `"end_turn"`: JSON checks need JSON, so point them at an object, not at a single text field). Nothing at a check's path fails just that check. On a recorded answer, a check's `answer_path` reads inside it when it's JSON. Each result says which `answer_path` it read. A `null` or blank value is an **empty answer**: the application answered with nothing, as a model does when it refuses or is cut off, and the run scores it like any other answer — most checks fail — so it counts in the results.

**When the call fails.** A wrong configuration shows up on the first run, clearly: a non-2xx reply, nothing at the output path, a non-JSON reply, an unresolvable `${VAR}`, or no URL at all makes the run `NotRan` with the reason in `error`, and nothing is evaluated. Only failures that say "try again" are retried — connection errors, timeouts, 5xx and 429 (honouring `Retry-After`) — with `max_retries` extra attempts and exponential backoff; a 400 or 401 is final on the first attempt.

**Where it stops.** Single-turn HTTP with JSON in and out — the large majority — is pure configuration. Streaming-only endpoints (most accept `"stream": false`), multi-turn applications needing a session first, non-HTTP transports and multi-step auth are protocol-level exceptions, each a small adapter in code when one is actually needed, never one interface per application.

## Worker

`assay.worker` is a separate Celery process from the API — it promotes a `Pending` `TestRunModel` to `Running` and then a terminal outcome (`Green`/`Amber`/`Red`/`NotRan`; see the Runs section above for what those mean). `execute_run`, its one registered task, does exactly that — fetches the run, evaluates every assigned test type, rolls up the outcome, writes it back. Every run-creation endpoint in `services/runs/` now dispatches it: right after the creating transaction commits, `_dispatch_runs` (`services/runs/_common.py`) publishes an `execute_run` task per run by name (`send_task`, not `.delay()` — so the API never has to import the worker's task or evaluator modules just to publish a message). A dispatch failure is caught and logged rather than surfacing to the caller, so run creation itself never fails because the broker is unreachable — the run just stays `Pending`. What's still missing is on the worker side: each assigned type is routed to the evaluator engine its catalogue row names (`GET /tests/types`' `engine`), and every result records the `engine` and `engine_settings` it was scored with — the five deterministic engines (Exact Match, Contains, Regex Match, the JSON checks and the length limits) score for real, and so do ROUGE, BLEU and METEOR (the `nlp` extra: `pip install -e ".[nlp]"`), while the other NLP-metric engines and the LLM-judge engine are still stubs that return one fixed result regardless of input. METEOR also needs NLTK's WordNet data (10 MB), downloaded once on each worker machine with `python -m nltk.downloader wordnet` (or `-d <dir>` plus `NLTK_DATA=<dir>` for a custom location); on python.org's macOS Python, run its "Install Certificates" script first or the download fails with `CERTIFICATE_VERIFY_FAILED`. A worker without it starts anyway, logs `METEOR checks will fail on this worker: …`, and fails just those checks; its scoring tests are skipped. The Regex Match engine needs the `regex` package (in the `worker` extra): unlike the stdlib `re`, it takes a per-match timeout, so a catastrophically backtracking pattern fails that one check instead of hanging a worker thread.

The worker process itself stays optional — the API runs fine, and every run-creation call still succeeds, whether or not a worker is actually running to pick anything up. `celery[redis]` is a base dependency of the API now (needed to publish), but talking to a real broker, running the worker process, and Postgres support for it (`psycopg2-binary`) are still behind the `worker` extra.

```bash
uv sync --extra worker
uv run celery -A assay.worker worker --loglevel=info
```

**Reconciliation scan (Celery Beat).** Direct dispatch covers the normal case; as a safety net for a run whose dispatch never reached the broker, a Beat process publishes a `reconcile_runs` task every `ASSAY_RECONCILIATION_INTERVAL_MINUTES` (default 60), which re-publishes `execute_run` for every `Pending` run older than `ASSAY_RECONCILIATION_PENDING_THRESHOLD_MINUTES` (default 15). Re-publishing a run that's actually still queued is harmless — the duplicate task's atomic claim matches nothing and no-ops. Run exactly **one** Beat process per environment (on Azure, a single-replica container), alongside the workers:

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
- Test runs — creation is fully implemented for every mode: standalone (`POST /runs/standalone/{test_id}`), live and replay for test sets (`POST /runs/test-sets/{test_set_id}`, `POST /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}`), and live and replay for test plans (`POST /runs/test-plans/{test_plan_id}`, `POST /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}`). A standalone run's own state can now be read back too (`GET /runs/standalone/{test_id}/test-runs`, `GET /runs/standalone/{test_id}/test-runs/{test_run_id}`), and so can a test set's and a test plan's past executions (`GET /runs/test-sets/{test_set_id}/executions`, `GET /runs/test-plans/{test_plan_id}/executions`), each with its `run_count` and `replayed_execution_id`. The audit trail goes further now: the runs a specific execution produced can be listed for both test sets and test plans (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs`, `GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs`), and a single run's full detail — including the frozen entry it ran against — can now be read back for both test sets (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}`) and test plans (`GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}`). The audit trail for both test sets and test plans is now complete end to end. A system-wide, origin-agnostic view now exists too: `GET /runs` paginates every run ever created regardless of how it was triggered — standalone, test-set-triggered, or test-plan-triggered alike — so a caller doesn't have to loop over every test, test set, and test plan individually to see what's running or recently ran. `GET /runs/executions` does the same for executions specifically — test set and test plan executions only, since a standalone run has no execution wrapper to aggregate — so a caller doesn't have to fan out one `GET /runs/{test-sets,test-plans}/{id}/executions` call per test set and test plan either. Every run-creation endpoint now dispatches its run(s) to `assay.worker` right after creation (see [Worker](#worker)), and `execute_run` does promote a run all the way to a terminal outcome (`Green`/`Amber`/`Red`/`NotRan`) when it runs — routing each assigned type to the engine its catalogue row names and recording that engine on the result — the deterministic engines (Exact Match, Contains, Regex Match) score for real; so do ROUGE, BLEU and METEOR; the other NLP-metric engines and the LLM-judge engine are still stubs. A test with no recorded `model_output` no longer sits idle: the run asks the application under test for the answer (see [Testing your application](#testing-your-application)) and every run records what it scored as `evaluated_output` with its `output_source`.
- LLM-as-judge evaluators and the broader statistics engine are stubbed.