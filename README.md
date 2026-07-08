# Assay

Evaluation toolkit for GenAI-powered applications. Helps testers measure model behaviour with NLP metrics, LLM-as-judge scoring, and statistical reporting over test runs.

## Status

Active development. Dataset CRUD operations, the z-test, test case management (create, list, get, update, delete), the test set layer (create, list, get, delete), and test set entries (snapshot tests into a set, list, get, update — until the entry has been run) are implemented. Deleting a test set cascades to its entries, but only while none of them have runs. LLM-as-judge evaluators, test plans, test runs, and the broader statistics engine are not yet built.

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
│   ├── stats.py         # Statistical test endpoints
│   └── meta.py          # Health check
├── schemas/             # Pydantic models — API validation and serialization
│   ├── __init__.py      # Re-exports all public models
│   ├── _common.py       # Shared base models (Pagination)
│   ├── datasets.py      # Dataset and row schemas
│   ├── test_sets.py     # Test set schemas (TestSetMetadata, PaginatedTestSetMetadataResponse, etc.)
│   ├── test_set_entries.py  # Test set entry schemas (TestSetEntryDetails, PaginatedTestSetEntriesDetails, etc.)
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
│   │   ├── _common.py                  # Shared helpers (_find_test_set_or_404, _find_test_set_entry_in_specific_test_set_or_404, _check_test_set_entry_has_no_runs_or_409, _check_test_set_entries_have_no_runs_or_409, etc.)
│   │   ├── create_test_set.py
│   │   ├── get_test_sets_metadata.py   # Paginated listing and single fetch by ID
│   │   ├── add_tests_to_test_set.py    # Snapshot tests into a set as entries
│   │   ├── get_test_sets_entries.py    # Paginated listing and single fetch of entries
│   │   ├── update_entry.py             # Partial update of an entry, until it has been run
│   │   └── delete_test_set.py          # Delete a set and cascade to its entries, until any has runs
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
    └── test.py          # TestModel, TestSetModel, TestSetEntryModel, TestPlanModel,
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
| `DELETE` | `/test-sets/{test_set_id}` | Delete a test set and all of its entries — blocked with a 409 if any entry has runs |
| `GET` | `/test-sets/{test_set_id}/entries` | List all entries (snapshotted tests) in a test set (paginated) |
| `GET` | `/test-sets/{test_set_id}/entries/{entry_id}` | Retrieve a single entry by ID |
| `POST` | `/test-sets/{test_set_id}/entries` | Snapshot one or more tests into a test set as entries |
| `PATCH` | `/test-sets/{test_set_id}/entries/{entry_id}` | Partially update an entry — only allowed until it has been run at least once (409 otherwise) |

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

Copy `.env.example` to `.env` and set the variable there for local development.

SQLite foreign key enforcement is enabled automatically on every connection via a `PRAGMA foreign_keys=ON` hook in `db.py`. This is required for `ON DELETE CASCADE` to work in SQLite.

Deleting a test set relies on this: its entries cascade-delete at the database level (`test_set_entries.test_set_id` has `ON DELETE CASCADE`, and the ORM relationship uses `passive_deletes=True` so it lets the database do it rather than nulling the FK itself). The `test_runs.test_set_entry_id` FK has no cascade, so if an entry still has a run, the database refuses the delete outright — a backstop behind the service-layer 409 check.

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
- Test set entries — removing a single entry from a set without deleting the whole set is not yet implemented (add, list, get, update, and deleting the entire set all are).
- Test plans and test runs — domain models are defined in `models/test.py` but service and API layers are not yet implemented.
- LLM-as-judge evaluators and the broader statistics engine are stubbed.