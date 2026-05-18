# Assay

Evaluation toolkit for GenAI-powered applications. Helps testers measure model behaviour with NLP metrics, LLM-as-judge scoring, and statistical reporting over test runs.

## Status

Early scaffold. The HTTP surface and domain models are defined; the evaluators and statistics engine are stubbed and return `501 Not Implemented` until filled in.

## Stack

- **Python** 3.11+
- **FastAPI** for the HTTP API
- **Pydantic v2** for validation and settings
- **SQLAlchemy 2 (async)** for ORM and database access
- **Alembic** for schema migrations
- **aiosqlite** — async SQLite driver (local dev)
- **asyncpg** — async PostgreSQL driver (production)

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
├── db.py                # Async SQLAlchemy engine and session dependency
├── api.py               # HTTP routes
├── stats.py             # Statistical test logic (z-test)
├── llm.py               # LLMClient protocol + AnthropicClient / OpenAIClient factory
├── schemas/             # Pydantic models — API validation and serialization
│   ├── __init__.py      # Re-exports all public models
│   ├── suite.py         # TestCase, TestSuite
│   ├── run.py           # MetricScore, TestCaseResult, StatisticalSummary, RunStatus, EvaluationRun
│   ├── evaluation.py    # JudgeCriterion, EvaluationRequest
│   └── stats.py         # ZTestRequest, ZTestResult
└── models/              # SQLAlchemy ORM models — database table definitions
    ├── __init__.py
    ├── base.py           # Shared DeclarativeBase
    ├── suite.py          # SuiteModel, TestCaseModel
    └── run.py            # EvaluationRunModel, TestCaseResultModel, MetricScoreModel
alembic/                 # Alembic migration environment
alembic.ini              # Alembic configuration (URL is read from ASSAY_DATABASE_URL at runtime)
tests/                   # Pytest suite
```

## Database

The database URL is controlled by `ASSAY_DATABASE_URL`. No code changes are needed between environments — only the URL changes.

| Environment | URL format |
|-------------|------------|
| Local (default) | `sqlite+aiosqlite:///./assay.db` |
| Production | `postgresql+asyncpg://user:pass@host:5432/assay` |

Copy `.env.example` to `.env` and set the variable there for local development.

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

## Statistical testing

### `POST /statistical-tests/z-test`

Tests whether a set of metric scores is statistically above (or below) a threshold, rather than just checking the average. This matters because a mean of 0.76 on 10 generations is much weaker evidence than a mean of 0.76 on 100 generations — the z-test quantifies that difference.

**When to use it:** after collecting NLP metric scores (ROUGE, BLEU, BERTScore, etc.) over a batch of AI generations, send the raw scores to this endpoint to get a statistically grounded pass/fail decision.

#### Request

| Field | Type | Default | Description |
|---|---|---|---|
| `scores` | `float[]` | required | Raw metric scores from individual generations |
| `threshold` | `float` | required | Minimum quality bar the population mean must clear |
| `alpha` | `float` | `0.05` | Significance level — `0.05` means 95% confidence |
| `alternative` | `string` | `"greater"` | `"greater"` \| `"less"` \| `"two-sided"` |

#### Response

| Field | Description |
|---|---|
| `passed` | `true` if there is sufficient statistical evidence to reject H₀ |
| `p_value` | Probability of observing this result if the true mean equalled the threshold; lower = stronger evidence |
| `z_statistic` | Standardised distance between the sample mean and the threshold |
| `confidence_interval` | Two-sided (1 − α) interval for the true population mean |
| `mean`, `std`, `n` | Sample descriptors |

#### Example

100 ROUGE-L scores from a generation pipeline, quality gate at 0.75:

```bash
curl -X POST http://127.0.0.1:8000/statistical-tests/z-test \
  -H "Content-Type: application/json" \
  -d '{
    "scores": [0.81, 0.78, 0.84, 0.76, 0.79, ...],
    "threshold": 0.75,
    "alpha": 0.05,
    "alternative": "greater"
  }'
```

```json
{
  "n": 100,
  "mean": 0.812,
  "std": 0.043,
  "threshold": 0.75,
  "alternative": "greater",
  "z_statistic": 14.42,
  "p_value": 0.000001,
  "alpha": 0.05,
  "passed": true,
  "confidence_interval": [0.804, 0.820]
}
```

**Interpreting the output:**
- `passed: true` — reject H₀ (mean = 0.75); the pipeline clears the quality gate with 95% confidence.
- `p_value: 0.000001` — if the true mean were exactly 0.75, there is a 0.0001% chance of observing a sample mean this high. Strong evidence.
- `confidence_interval: [0.804, 0.820]` — the true population mean almost certainly sits between 0.804 and 0.820, well above 0.75.

#### Notes

- Reliable for **n ≥ 30**. Below that the normal approximation breaks down; a t-test would be more appropriate.
- Uses the **sample standard deviation** (Bessel-corrected, n − 1 denominator) as an estimate of the population std.
- The confidence interval is always **two-sided at (1 − α)** regardless of `alternative`, since it describes where the true mean lies rather than the test direction.
- Implemented with Python's `statistics.NormalDist` — no external dependencies.

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
