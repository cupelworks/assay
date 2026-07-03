# Assay

Evaluation toolkit for GenAI-powered applications. Helps testers measure model behaviour with NLP metrics, LLM-as-judge scoring, and statistical reporting over test runs.

## Status

Active development. Dataset CRUD operations, the z-test, test case management (create, list, get, update, delete), and the test set layer (create, list, get) are implemented. LLM-as-judge evaluators, test plans, test runs, and the broader statistics engine are not yet built.

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
│   ├── test_sets.py     # Test set endpoints
│   ├── stats.py         # Statistical test endpoints
│   └── meta.py          # Health check
├── schemas/             # Pydantic models — API validation and serialization
│   ├── __init__.py      # Re-exports all public models
│   ├── _common.py       # Shared base models (Pagination)
│   ├── datasets.py      # Dataset and row schemas
│   ├── test_sets.py     # Test set schemas (TestSetMetadata, PaginatedTestSetMetadataResponse, etc.)
│   ├── tests.py         # Test case schemas (CreateTestCaseRequest, PaginatedTestCases, etc.)
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
│   │   ├── _common.py                  # Shared helpers (_find_test_set_or_404, _check_unique_test_set_name_or_409)
│   │   ├── create_test_set.py
│   │   └── get_test_sets_metadata.py   # Paginated listing and single fetch by ID
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
- Test set entries — adding/removing tests from a set, and retrieving the snapshotted entries within a set.
- Test plans and test runs — domain models are defined in `models/test.py` but service and API layers are not yet implemented.
- LLM-as-judge evaluators and the broader statistics engine are stubbed.