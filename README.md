# Assay

Evaluation toolkit for GenAI-powered applications. Helps testers measure model behaviour with NLP metrics, LLM-as-judge scoring, and statistical reporting over test runs.

## Status

Active development. Dataset CRUD operations, the z-test, test case management (create, list, get, update, delete), the test set layer (create, list, get, rename, delete), and test set entries (snapshot tests into a set, list, get, update — until the entry has been run, delete individual entries or the whole set, or unlink individual entries from the set without deleting them) are implemented. Deleting a test set cascades to its entries, but only while none of them have runs; bulk-deleting individual entries enforces the same guard and is all-or-nothing. Unlinking entries has no such guard — it's the operation for detaching a run-having entry from a set's membership without touching its frozen content or execution history. Test plans are mostly implemented (create, list, get, rename, list the test sets included in a plan, link test sets to a plan, and unlink test sets from a plan — unlinking is always allowed, even if the test set already has runs recorded via this plan); deleting a plan and executing it are not yet built. The schema-level groundwork for grouping runs by execution — live fan-out vs. replaying a specific past execution, for both test plans and standalone test sets — is in place, but nothing yet creates a run: LLM-as-judge evaluators, test execution itself, and the broader statistics engine are not yet built.

## Stack

- **Python** 3.11+
- **FastAPI** for the HTTP API
- **Pydantic v2** for validation and settings
- **SQLAlchemy 2 (async)** for ORM and database access
- **Alembic** for schema migrations
- **aiosqlite** — async SQLite driver (local dev)
- **asyncpg** — async PostgreSQL driver (production)
- **Prometheus FastAPI Instrumentator** — request metrics at `GET /metrics`

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
| `ASSAY_LLM_PROVIDER` | *(unset)* | Reserved for the LLM-as-judge evaluator's provider selection (`anthropic` or `openai`). Not yet read anywhere — `Settings` in `config.py` has no field for it yet, since the evaluator itself isn't implemented |
| `ASSAY_LLM_MODEL` | *(unset)* | Reserved for the LLM-as-judge evaluator's model selection. Same caveat as `ASSAY_LLM_PROVIDER` |
| `ANTHROPIC_API_KEY` | *(unset)* | Will be needed once the Anthropic LLM-as-judge evaluator ships. Install the optional extra ahead of time with `pip install -e ".[anthropic]"` (or `uv sync --extra anthropic`) |
| `OPENAI_API_KEY` | *(unset)* | Will be needed once the OpenAI LLM-as-judge evaluator ships. Install with the `openai` extra, same pattern as above |

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
│   │   ├── _common.py                  # Shared helpers (_check_unique_test_plan_name_or_409, _find_test_plan_by_id_or_404, _check_test_set_not_in_test_plan_or_409, _find_test_plan_entries_or_404)
│   │   ├── create_test_plan.py
│   │   ├── get_test_plans_metadata.py         # Paginated listing and single fetch by ID
│   │   ├── get_test_plan_entries_metadata.py  # Paginated listing of the test sets included in a plan
│   │   ├── update_test_plan.py                # Rename, with a self-name no-op guard around the uniqueness check
│   │   ├── add_test_sets_to_test_plan.py      # Link one or more test sets to a plan, deduplicated via the existence-check query
│   │   └── remove_test_set_from_test_plan.py  # Unlink one or more test sets from a plan — all-or-nothing; unconditional even if the test set already has runs via this plan
│   ├── tests/
│   │   ├── _common.py                  # Shared helpers (_find_all_tests_or_404, _find_test_by_id_or_404, _validate_test_type_name)
│   │   ├── create_new_test.py          # Manual creation and bulk creation from dataset
│   │   ├── get_tests.py                # Paginated listing and single fetch by ID
│   │   ├── update_test.py              # Partial update with field-level null semantics
│   │   └── delete_test.py              # Bulk delete with referential integrity checks
│   └── stats.py         # run_z_test
└── models/              # SQLAlchemy ORM models — database table definitions
    ├── __init__.py
    ├── base.py          # Shared DeclarativeBase
    ├── datasets.py      # DatasetModel, DatasetRowModel
    ├── stats.py         # StatisticalVerificationModel
    └── test.py          # TestModel, TestSetModel, TestSetEntryModel, TestSetExecutionModel,
                         # TestPlanModel, TestPlanEntryModel, TestPlanExecutionModel,
                         # TestRunModel, and related junction tables
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
| `PUT` | `/datasets/rows` | Replace all rows in a dataset |
| `PATCH` | `/datasets/name` | Rename a dataset |
| `PATCH` | `/datasets/rows` | Update existing rows by ID |
| `DELETE` | `/datasets` | Delete a dataset and all its rows |
| `DELETE` | `/datasets/rows` | Delete specific rows by ID |

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
| `GET` | `/test-plans/{test_plan_id}/entries` | List the test sets included in a test plan (paginated); use each item's `test_set.id` with `GET /test-sets/{test_set_id}/entries` to fetch that set's snapshotted tests |
| `POST` | `/test-plans/{test_plan_id}/entries` | Link one or more test sets to a test plan — blocked with a 409 if any is already linked to this plan |
| `DELETE` | `/test-plans/{test_plan_id}/entries` | Unlink one or more test sets from a test plan — all-or-nothing (404 if any requested test set isn't linked to this plan); always allowed even if the test set already has runs recorded via this plan |

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

`test_set_entries.test_set_id` is nullable, which is what makes unlinking possible: unlinking sets it to `NULL` directly rather than deleting the row, detaching the entry from the set while leaving the row (and any runs pointing at it) in place.

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

- `services/datasets/replace_dataset_content.py` — Consider what happens when a dataset row has a relationship with executed tests (cascading deletes or constraint violations on full replacement).
- Test plans — deleting a plan and executing it are not yet implemented. Create, list, get, rename, listing a plan's test sets, linking test sets to a plan, and unlinking test sets from a plan are.
- Test runs — the `TestRunModel` domain model exists, including attribution FKs for which test, test set entry, test set execution, or test plan execution produced it, and is already referenced by the "has runs" guards on test and test set entry deletion. `TestPlanExecutionModel` and `TestSetExecutionModel` (grouping runs by trigger event — live fan-out vs. replaying a specific past execution) exist too. None of this is wired to a service or API layer yet — nothing anywhere creates a `TestRunModel` row.
- LLM-as-judge evaluators and the broader statistics engine are stubbed.