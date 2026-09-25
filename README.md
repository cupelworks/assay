# Assay

Evaluation toolkit for GenAI-powered applications. Helps testers measure model behaviour with NLP metrics, LLM-as-judge scoring, and statistical reporting over test runs.

## Status

Active development. Dataset CRUD operations, the z-test, test case management (create, list, get, update, delete), the test set layer (create, list, get, rename, delete), and test set entries (snapshot tests into a set, list, get, update — until the entry has been run, delete individual entries or the whole set, or unlink individual entries from the set without deleting them) are implemented. Deleting a test set cascades to its entries, but only while none of them have runs; bulk-deleting individual entries enforces the same guard and is all-or-nothing. Unlinking entries has no such guard — it's the operation for detaching a run-having entry from a set's membership without touching its frozen content or execution history. Test plans are mostly implemented (create, list, get, rename, delete, list the test sets included in a plan, link test sets to a plan, and unlink test sets from a plan — unlinking is always allowed, even if the test set already has runs recorded via this plan). Deleting a plan cascades to its own links to test sets (never blocked, mirroring the unlink behavior), but is permanently blocked once the plan has ever been executed, so a run's audit trail can never lose track of which campaign produced it. The schema-level groundwork for grouping runs by execution — live fan-out vs. replaying a specific past execution — is in place for both test plans and standalone test sets, and is now fully wired up end-to-end for both test sets and test plans (see below). Standalone runs can now be created (`POST /runs/standalone/{test_id}`) — exactly one `TestRunModel` row per call, in `Pending` status, blocked with a 409 if the test has no test types assigned. Live test-set execution can now be triggered too (`POST /runs/test-sets/{test_set_id}`) — one `TestSetExecutionModel` plus one `Pending` `TestRunModel` per entry currently in the set, blocked with a 409 if the set has no entries or if any entry has no test types assigned. A past test-set execution can now be replayed as well (`POST /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}`) — a new `TestSetExecutionModel` plus one `Pending` `TestRunModel` per entry the replayed execution ran, targeting the exact same frozen entries regardless of the set's current membership; blocked with a 404 if the execution doesn't exist or isn't linked to this test set, or a 409 if it has zero runs to replay. Live test-plan execution can now be triggered too (`POST /runs/test-plans/{test_plan_id}`) — one `TestPlanExecutionModel` plus one `Pending` `TestRunModel` per entry across every test set currently linked to the plan, blocked with a 409 if the plan has no linked test sets, if any linked test set has no entries, or if any entry has no test types assigned. A past test-plan execution can now be replayed as well (`POST /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}`) — a new `TestPlanExecutionModel` plus one `Pending` `TestRunModel` per entry the replayed execution ran, targeting the exact same frozen entries regardless of the plan's current linked test sets; blocked with a 404 if the execution doesn't exist or isn't linked to this test plan, or a 409 if it has zero runs to replay. A standalone run's own state can now be read back too: `GET /runs/standalone/{test_id}/test-runs` paginates every standalone run created for a test, and `GET /runs/standalone/{test_id}/test-runs/{test_run_id}` returns one run's full detail, including `results`/`error`/`executed_at` once populated. There's also a system-wide view spanning every origin: `GET /runs` paginates every run ever created — standalone, test-set-triggered, and test-plan-triggered alike, newest first — with each item's `origin` field (`Standalone`, `TestSet`, or `TestPlan`) determining which of `test_case_id`, or the `test_set_entry_id` + `test_set_execution_id`/`test_plan_execution_id` pair, is populated. `GET /runs/executions` is the equivalent system-wide view for executions rather than runs — every `TestSetExecutionModel`/`TestPlanExecutionModel` ever triggered, across every test set and test plan, newest first — but deliberately excludes standalone runs, since a standalone run has no execution wrapper to aggregate in the first place. A test set's and a test plan's past executions can now be listed as well: `GET /runs/test-sets/{test_set_id}/executions` and `GET /runs/test-plans/{test_plan_id}/executions` each paginate every execution (live or replayed) ever triggered for the set/plan, with a `run_count` rolling up how many `TestRunModel` rows it produced. The runs a specific execution produced can now be listed too, for both test sets (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs`) and test plans (`GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs`), and now a specific run's full detail can be read back for both test sets (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}`) and test plans (`GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}`), including the frozen test set entry it ran against in both cases. The test-plan version returns a nullable `test_set_id` — the entry's current test set, `null` once it's been unlinked — rather than a path parameter like the test-set version, since one plan execution spans every test set linked to the plan; both versions stay reachable even after the entry is unlinked from its set, and the test-plan one also stays reachable if that set is later unlinked from the plan, since plan-to-set links never freeze. Nothing yet promotes a pending run to `Running` or a terminal outcome (`Green`/`Amber`/`Red`/`NotRan`): the actual execution/scoring mechanism, LLM-as-judge evaluators, and the broader statistics engine are not yet built.

## Stack

- **Python** 3.11+
- **FastAPI** for the HTTP API
- **Pydantic v2** for validation and settings
- **SQLAlchemy 2 (async)** for ORM and database access
- **Alembic** for schema migrations
- **aiosqlite** — async SQLite driver (local dev)
- **asyncpg** — async PostgreSQL driver (production)
- **Prometheus FastAPI Instrumentator** — request metrics at `GET /metrics`
- **Celery** — task queue for the run-execution worker (optional `worker` extra; infra only for now, no tasks yet — see [Worker](#worker))

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

Copy `.env.example` to `.env` to configure the app locally. Every variable is optional — all have working defaults except the database URL in production (see [Database](#database)).

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSAY_HOST` | `127.0.0.1` | Host the dev server binds to (`main.py`'s `uvicorn.run`, used when running `python -m assay.main` directly — not consulted when running via the `uvicorn assay.main:app` CLI shown above) |
| `ASSAY_PORT` | `8000` | Port the dev server binds to, same caveat as `ASSAY_HOST` |
| `ASSAY_LOG_LEVEL` | `INFO` | Only currently wired to one thing: setting this to `DEBUG` turns on SQLAlchemy engine echo, logging every SQL statement. Not yet a general application log level |
| `ASSAY_DATABASE_URL` | `sqlite+aiosqlite:///./assay.db` | Database connection string — see [Database](#database) for the production format |
| `ASSAY_CORS_ALLOWED_ORIGINS` | `http://localhost:4200` | Comma-separated list of origins allowed to make cross-origin requests (e.g. `https://app.example.com,https://staging.example.com`). Credentialed requests (cookies, auth headers) are only allowed when the list isn't `*` — browsers reject `Access-Control-Allow-Credentials` paired with a wildcard origin |
| `ASSAY_LLM_PROVIDER` | *(unset)* | Reserved for the LLM-as-judge evaluator's provider selection (`anthropic` or `openai`). Not yet read anywhere — `Settings` in `config.py` has no field for it yet, since the evaluator itself isn't implemented |
| `ASSAY_LLM_MODEL` | *(unset)* | Reserved for the LLM-as-judge evaluator's model selection. Same caveat as `ASSAY_LLM_PROVIDER` |
| `ANTHROPIC_API_KEY` | *(unset)* | Will be needed once the Anthropic LLM-as-judge evaluator ships. Install the optional extra ahead of time with `pip install -e ".[anthropic]"` (or `uv sync --extra anthropic`) |
| `OPENAI_API_KEY` | *(unset)* | Will be needed once the OpenAI LLM-as-judge evaluator ships. Install with the `openai` extra, same pattern as above |
| `ASSAY_CELERY_BROKER_URL` | `redis://localhost:6379/0` | Only read by the worker process (`assay.worker`), never the API — see [Worker](#worker) for the full set of options (local Redis, local SQLite with nothing to install, production Azure Cache for Redis) |
| `ASSAY_CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Same scope as `ASSAY_CELERY_BROKER_URL`, see [Worker](#worker) |

## Project layout

```
src/assay/
├── main.py              # FastAPI app factory
├── config.py            # Pydantic settings (reads ASSAY_* env vars)
├── db.py                # Async SQLAlchemy engine, session dependency, SQLite FK pragma
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
│   │   ├── create_new_run.py           # Create a standalone pending run (one row regardless of test type count, 409 if none assigned), trigger a live test-set run (one execution + one row per entry, 409 if the set has no entries or if any entry has no test types assigned), replay a past test-set execution (one new execution + one row per entry the replayed execution ran, 404 if the execution doesn't exist or isn't linked to this set, 409 if it has zero runs), trigger a live test-plan run (one execution + one row per entry across every linked test set, 409 if the plan has no linked test sets, a linked set has no entries, or any entry has no test types assigned), or replay a past test-plan execution (one new execution + one row per entry the replayed execution ran, 404 if the execution doesn't exist or isn't linked to this plan, 409 if it has zero runs)
│   │   ├── get_run_metadata.py         # Paginated listing of every standalone run created for a test, every execution triggered for a test set or test plan, or every run a specific test-set/test-plan execution produced
│   │   └── get_run_details.py          # Full detail for a single standalone run or a single test-set-execution run, including results/error/executed_at once populated
│   └── stats.py         # run_z_test
├── models/              # SQLAlchemy ORM models — database table definitions
│   ├── __init__.py
│   ├── base.py          # Shared DeclarativeBase
│   ├── datasets.py      # DatasetModel, DatasetRowModel
│   ├── stats.py         # StatisticalVerificationModel
│   └── test.py          # TestModel, TestSetModel, TestSetEntryModel, TestSetExecutionModel,
│                        # TestPlanModel, TestPlanEntryModel, TestPlanExecutionModel,
│                        # TestRunModel, and related junction tables
└── worker/              # Celery app — infra only, no tasks registered yet (see Worker below)
    ├── __init__.py      # Re-exports `app` so `celery -A assay.worker worker` resolves it
    └── celery_app.py    # Celery() instance, broker/backend from settings, rediss:// TLS handling
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
| `GET` | `/tests` | List all test cases (paginated, includes total count) |
| `GET` | `/tests/{test_case_id}` | Retrieve a single test case by ID |
| `POST` | `/tests` | Create a single test case manually |
| `POST` | `/tests/from-dataset` | Bulk-create test cases from all rows in a dataset |
| `PATCH` | `/tests/{test_case_id}` | Partially update a test case — only sent fields are changed; unknown fields are rejected |
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
| `PATCH` | `/test-sets/{test_set_id}/entries/{entry_id}` | Partially update an entry — only allowed until it has been run at least once (409 otherwise) |
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
| `POST` | `/runs/standalone/{test_id}` | Create a standalone, pending run for a live test — enqueue-only, does not execute anything. Blocked with a 409 if the test has no test types assigned |
| `POST` | `/runs/test-sets/{test_set_id}` | Trigger a live execution of a test set — one pending run per entry currently in the set, grouped under a new test set execution. Enqueue-only. Blocked with a 409 if the set has no entries or if any entry has no test types assigned |
| `POST` | `/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}` | Replay a past test set execution — one pending run per entry that execution ran, targeting the exact same frozen entries regardless of the set's current membership, grouped under a new test set execution. Enqueue-only. Blocked with a 404 if the execution doesn't exist or isn't linked to this test set, or a 409 if it has zero runs to replay |
| `POST` | `/runs/test-plans/{test_plan_id}` | Trigger a live execution of a test plan — one pending run per entry across every test set currently linked to the plan, grouped under a new test plan execution. Enqueue-only. Blocked with a 409 if the plan has no linked test sets, if any linked test set has no entries, or if any entry has no test types assigned |
| `POST` | `/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}` | Replay a past test plan execution — one pending run per entry that execution ran, targeting the exact same frozen entries regardless of the plan's current linked test sets, grouped under a new test plan execution. Enqueue-only. Blocked with a 404 if the execution doesn't exist or isn't linked to this test plan, or a 409 if it has zero runs to replay |
| `GET` | `/runs` | Paginated list of every run in the system, regardless of origin (standalone, test-set-triggered, or test-plan-triggered) — `id`, `status`, `created_at`, `origin`, plus the origin-specific ID(s) (`test_case_id`, or `test_set_entry_id` + `test_set_execution_id` + `test_set_id`, or `test_set_entry_id` + `test_plan_execution_id` + `test_plan_id`) per item, newest first. `test_set_id`/`test_plan_id` let a caller deep-link a row without a separate lookup. Optional `?status=` filters to one status (`Pending`/`Running`/`Green`/`Amber`/`Red`/`NotRan`); optional `?origin=` filters to one origin (`Standalone`/`TestSet`/`TestPlan`); both combine |
| `GET` | `/runs/executions` | Paginated list of every execution across every test set and test plan (live or replayed) — `id`, `created_at`, `origin` (`TestSet`/`TestPlan`), `run_count`, plus the origin-specific ID(s) (`test_set_id`/`test_plan_id`, and `replayed_test_set_execution_id`/`replayed_test_plan_execution_id` if it was a replay) per item, newest first |
| `GET` | `/runs/standalone/{test_id}/test-runs` | Paginated list of every standalone run ever created for a test — `id`, `status`, `created_at`, `test_case_id` per item, newest first |
| `GET` | `/runs/standalone/{test_id}/test-runs/{test_run_id}` | Full detail for a single standalone run — `id`, `status`, `created_at`, `test_case_id`, plus `results`/`error`/`executed_at`, which stay null until the run reaches a terminal status |
| `GET` | `/runs/test-sets/{test_set_id}/executions` | Paginated list of every execution (live or replayed) ever triggered for a test set — `id`, `created_at`, `test_set_id`, `run_count`, `replayed_execution_id` per item, newest first |
| `GET` | `/runs/test-plans/{test_plan_id}/executions` | Paginated list of every execution (live or replayed) ever triggered for a test plan — `id`, `created_at`, `test_plan_id`, `run_count`, `replayed_execution_id` per item, newest first |
| `GET` | `/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs` | Paginated list of every run a specific test set execution produced — `id`, `status`, `created_at`, `test_set_entry_id`, `test_set_execution_id` per item, newest first. 404 if the test set or execution doesn't exist, or the execution isn't linked to this test set |
| `GET` | `/runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}` | Full detail for a single run produced by a test set execution, including `results`/`error`/`executed_at` and the frozen test set entry it ran against (`test_case_id`, `name`, `input`, `expected_output`, `model_output`, `test_type_assignments`, `test_case_snapshot_at`). 404 if the test set, execution, or run doesn't exist, or if any of them aren't linked to the one above it in the path |
| `GET` | `/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs` | Paginated list of every run a specific test plan execution produced — `id`, `status`, `created_at`, `test_set_entry_id`, `test_plan_execution_id` per item, newest first. 404 if the test plan or execution doesn't exist, or the execution isn't linked to this test plan |
| `GET` | `/runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}` | Full detail for a single run produced by a test plan execution, including `results`/`error`/`executed_at` and the frozen test set entry it ran against (`test_case_id`, `name`, `input`, `expected_output`, `model_output`, `test_type_assignments`, `test_case_snapshot_at`). Also returns `test_plan_id` and a nullable `test_set_id` — the entry's *current* test set, `null` if it's since been unlinked; unlike the test-set version, there's no `test_set_id` in the path, since one plan execution spans every test set linked to the plan. 404 if the test plan, execution, or run doesn't exist, or if any of them aren't linked to the one above it in the path |

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

## Worker

`assay.worker` is a separate Celery process from the API — it exists so a future background task can promote a `Pending` `TestRunModel` to `Running` and then a terminal outcome (`Green`/`Amber`/`Red`/`NotRan`; see the Runs section above for what those mean). Right now it's infrastructure only: the Celery app is fully configured and runnable, but no task is registered, so nothing actually happens yet.

It's an optional piece — the API never imports `celery` and runs fine whether or not the worker is set up at all.

```bash
uv sync --extra worker
uv run celery -A assay.worker worker --loglevel=info
```

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

There's nothing to see here until a task actually exists — right now `assay.worker` has none registered (see above), so Flower's task list stays empty regardless of how many runs the API creates.

## Observability

Assay exposes a Prometheus-compatible scrape endpoint at `GET /metrics`. It is not listed in the OpenAPI docs (`/docs`) because it returns plain text rather than JSON, but it is active on every running instance.

```bash
curl http://127.0.0.1:8000/metrics
```

The endpoint provides request count and latency histograms (`http_requests_total`, `http_request_duration_seconds`) labelled by method, status code, and handler. Point any Prometheus scraper — or a compatible platform like Datadog, Grafana Cloud, or Google Cloud Managed Prometheus — at this URL.

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
- Test runs — creation is fully implemented for every mode: standalone (`POST /runs/standalone/{test_id}`), live and replay for test sets (`POST /runs/test-sets/{test_set_id}`, `POST /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}`), and live and replay for test plans (`POST /runs/test-plans/{test_plan_id}`, `POST /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}`). A standalone run's own state can now be read back too (`GET /runs/standalone/{test_id}/test-runs`, `GET /runs/standalone/{test_id}/test-runs/{test_run_id}`), and so can a test set's and a test plan's past executions (`GET /runs/test-sets/{test_set_id}/executions`, `GET /runs/test-plans/{test_plan_id}/executions`), each with its `run_count` and `replayed_execution_id`. The audit trail goes further now: the runs a specific execution produced can be listed for both test sets and test plans (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs`, `GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs`), and a single run's full detail — including the frozen entry it ran against — can now be read back for both test sets (`GET /runs/test-sets/{test_set_id}/executions/{test_set_execution_id}/test-runs/{test_run_id}`) and test plans (`GET /runs/test-plans/{test_plan_id}/executions/{test_plan_execution_id}/test-runs/{test_run_id}`). The audit trail for both test sets and test plans is now complete end to end. A system-wide, origin-agnostic view now exists too: `GET /runs` paginates every run ever created regardless of how it was triggered — standalone, test-set-triggered, or test-plan-triggered alike — so a caller doesn't have to loop over every test, test set, and test plan individually to see what's running or recently ran. `GET /runs/executions` does the same for executions specifically — test set and test plan executions only, since a standalone run has no execution wrapper to aggregate — so a caller doesn't have to fan out one `GET /runs/{test-sets,test-plans}/{id}/executions` call per test set and test plan either. Still missing, independent of any of the above: nothing promotes a `pending` run to `running` or a terminal outcome (`green`/`amber`/`red`/`not_ran`). A worker process exists now (`assay.worker`, see [Worker](#worker)) but has no task registered yet — nothing calls a model, scores it, or writes back `results`/`error`/`executed_at`.
- LLM-as-judge evaluators and the broader statistics engine are stubbed.