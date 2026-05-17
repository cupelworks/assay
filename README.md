# Assay

Evaluation toolkit for GenAI-powered applications. Helps testers measure model behaviour with NLP metrics, LLM-as-judge scoring, and statistical reporting over test runs.

## Status

Early scaffold. The HTTP surface and domain models are defined; the evaluators and statistics engine are stubbed and return `501 Not Implemented` until filled in.

## Stack

- **Python** 3.11+
- **FastAPI** for the HTTP API
- **Pydantic v2** for validation and settings

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
├── api.py               # HTTP routes
└── schemas/
    ├── __init__.py      # Re-exports all public models
    ├── suite.py         # TestCase, TestSuite
    ├── run.py           # MetricScore, TestCaseResult, StatisticalSummary, RunStatus, EvaluationRun
    └── evaluation.py    # JudgeCriterion, EvaluationRequest
tests/                   # Pytest suite
```

## Tests

```bash
uv run pytest
```
