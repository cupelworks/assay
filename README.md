# Assay

Test how a GenAI-powered application behaves, and keep a reproducible record of it.

Bring the prompts you send your application, the answers it gave and the answers you
expected. Assay scores each case with the checks you assign — deterministic rules, NLP
metrics against a threshold, LLM-as-judge verdicts against a rubric — organises cases into
stable test sets and plans, and records every run's outcome.

## License

Copyright (C) 2026 Francesco Campanile.

Assay is free software: you can redistribute it and/or modify it under the terms of the
[GNU Affero General Public License, version 3](LICENSE) (`AGPL-3.0-only`), as published by
the Free Software Foundation. It is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full terms.

Every source file carries this notice in its first two lines. If you run a modified Assay
for others to use over a network, the AGPL asks you to offer them its source code.

## Contents

- [What it does](#what-it-does)
- [Concepts](#concepts)
- [Quickstart](#quickstart)
- [Configuration](#configuration)
- [Testing your application](#testing-your-application)
- [Judging with an LLM](#judging-with-an-llm)
- [Running with statistics](#running-with-statistics)
- [API](#api)
- [Worker](#worker)
- [Observability](#observability)
- [Database](#database)
- [Development](#development)

## What it does

- **Checks of every kind.** Deterministic (Exact Match, Contains, Regex, JSON, length
  limits), NLP metrics (ROUGE, BLEU, METEOR, BERTScore, Cosine Similarity) and LLM judges
  (Correctness, Relevance, Bias, Toxicity, Hallucination). The catalogue is data:
  `GET /tests/types` lists every check type and its settings.
- **Recorded or live answers.** A test can carry the answer your application gave, or
  Assay asks your application for it at run time, over HTTP, configured — not coded.
- **Reproducible history.** Tests are copied into test sets as entries; an entry that has
  run never changes, and a past execution can be replayed against exactly the same
  entries. Every run records what it scored and how.
- **Test plans.** Group test sets into a campaign and run them together.
- **Run with statistics.** Run a test, set or plan N times and get a pass / fail /
  inconclusive verdict per check at a stated confidence; compare two batches.
- **Lists built for a UI.** Search, filters, sorts and facet counts on every list; each
  item says how it stands (latest run, latest verdict, whether it can still be deleted).
- **Production-minded.** Async API, a separate Celery worker, JSON logs with request
  IDs, Prometheus metrics, SQLite locally and PostgreSQL in production.

## Concepts

| Term | Meaning |
|---|---|
| **Dataset** | Rows of `prompt`, `expected_output` and `model_output`, imported from a `.jsonl` file. Tests can be made from its rows. |
| **Test** | An input, an optional expected answer, an optional recorded answer, and the checks assigned to it. Always editable. |
| **Check** | A check type from the catalogue with its settings (a threshold, a pattern, a rubric…) and a label unique within the test. |
| **Test set** | A named group of **entries**: copies of tests. An entry freezes once it has run. |
| **Test plan** | A named group of links to test sets, run together. |
| **Run** | One evaluation of a test or entry, scored by the worker. |
| **Execution** | One run of a whole set or plan: one run per entry, *live* (the entries now) or a *replay* of a past execution. |
| **Batch** | A test, set or plan run N times for a statistical verdict. **Comparison**: two batches compared check by check. |

A run's status moves from `Pending` to `Running`, then to one of:

| Status | Meaning |
|---|---|
| `Green` | Every check passed. |
| `Amber` | Some checks passed, some didn't. |
| `Red` | Every check was evaluated and none passed. |
| `NotRan` | Nothing could be evaluated (e.g. the application didn't answer); `error` says why. |

## Quickstart

Requires Python 3.11+. With pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn assay.main:app --reload
```

Or with [uv](https://docs.astral.sh/uv/): `uv sync --extra dev`, then prefix the commands
with `uv run`.

Open <http://127.0.0.1:8000/docs> for the interactive API.

Runs are scored by a separate worker process, which needs Redis — see [Worker](#worker).
Without one, the API works and runs wait as `Pending`.

## Configuration

Copy `.env.example` to `.env`. Every variable is optional; the defaults work locally.

| Variable | Default | What it sets |
|---|---|---|
| `ASSAY_DATABASE_URL` | `sqlite+aiosqlite:///./assay.db` | The database; `postgresql+asyncpg://…` in production |
| `ASSAY_LOG_LEVEL` | `INFO` | Level of Assay's own loggers; `DEBUG` also logs SQL |
| `ASSAY_LOG_FORMAT` | `text` | `text` for a terminal, `json` for a log collector |
| `ASSAY_HOST` / `ASSAY_PORT` | `127.0.0.1` / `8000` | Where `python -m assay.main` listens |
| `ASSAY_CORS_ALLOWED_ORIGINS` | `http://localhost:4200` | Comma-separated origins allowed to call the API from a browser |
| `ASSAY_TARGET_URL` | *(unset)* | Your application's endpoint — see [Testing your application](#testing-your-application) |
| `ASSAY_TARGET_METHOD` | `POST` | `POST`, `PUT` or `PATCH` |
| `ASSAY_TARGET_HEADERS` | `{}` | JSON headers; values may reference `${ENV_VAR}` |
| `ASSAY_TARGET_BODY` | `{"input": "{{input}}"}` | JSON body template; `{{input}}` is replaced by the test's input |
| `ASSAY_TARGET_OUTPUT_PATH` | `$.output` | JSONPath to the answer in the reply |
| `ASSAY_TARGET_TIMEOUT_SECONDS` / `_MAX_RETRIES` | `60` / `2` | Per-call timeout and retries |
| `ASSAY_JUDGE_PROVIDER` | *(unset)* | `anthropic` or `openai` (any OpenAI-compatible server) — see [Judging with an LLM](#judging-with-an-llm) |
| `ASSAY_JUDGE_MODEL` | *(unset)* | The judge's model, required with a provider |
| `ASSAY_JUDGE_URL` | *(unset)* | The endpoint, exactly as written; unset for the provider's own |
| `ASSAY_JUDGE_API_KEY_ENV` | *(unset)* | The *name* of the variable holding the key |
| `ASSAY_JUDGE_TIMEOUT_SECONDS` / `_MAX_RETRIES` | `60` / `2` | Per-call timeout and retries |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | *(unset)* | The judge's key, read by the worker; never stored or returned |
| `ASSAY_CELERY_BROKER_URL` / `_RESULT_BACKEND` | `redis://localhost:6379/0` | The worker's broker and result backend |
| `ASSAY_WORKER_DB_POOL_SIZE` / `_MAX_OVERFLOW` | `10` / `10` | The worker's database connection pool |
| `ASSAY_RECONCILIATION_INTERVAL_MINUTES` | `60` | How often Beat re-sends runs whose dispatch was lost |
| `ASSAY_RECONCILIATION_PENDING_THRESHOLD_MINUTES` | `15` | How old a `Pending` run must be to be re-sent |

The application and judge settings can also be saved from the UI (`/settings/target`,
`/settings/judge`): saved settings win over the environment until reset.

## Testing your application

Assay tests an application, not a bare model: it scores what your application answers,
with its system prompt, retrieval and tools. A test either carries that answer
(`model_output`, scored as it is) or has none, and then the run calls your application's
own endpoint and scores the reply.

Describe your application's API once, in `.env` or on the Settings page:

```dotenv
ASSAY_TARGET_URL=https://my-app.example.com/api/chat
ASSAY_TARGET_HEADERS={"Authorization": "Bearer ${ASSAY_TARGET_API_KEY}"}
ASSAY_TARGET_BODY={"messages": [{"role": "user", "content": "{{input}}"}], "stream": false}
ASSAY_TARGET_OUTPUT_PATH=$.choices[0].message.content
```

| Application's API | Body | Output path |
|---|---|---|
| OpenAI-compatible chat | `{"messages": [{"role": "user", "content": "{{input}}"}]}` | `$.choices[0].message.content` |
| Custom Q&A | `{"question": "{{input}}"}` | `$.answer` |
| Nested envelope | `{"payload": {"text": "{{input}}"}}` | `$.data.reply.text` |
| Assay's defaults | `{"input": "{{input}}"}` | `$.output` |

- **Secrets** go in variables the headers reference (`${ASSAY_TARGET_API_KEY}`), never in
  the settings; they're never logged or returned, and neither are prompts or answers.
- **Check the settings** before a run depends on them: `POST /settings/target/checks`
  asks a worker to call your application once and reports the answer or why it failed.
- **The answer.** A string at the output path is the answer; an object or array is the
  answer as JSON text. `$` makes the whole reply the answer.
- **Other parts of the reply.** The run keeps the whole reply (`application_reply`), and
  a check can read another part with its own `answer_path`, e.g. `$.stop_reason`.
- **Every run records** the answer it scored (`evaluated_output`) and where it came from
  (`output_source`: `recorded` or `application`).
- **Failures.** A failed call makes the run `NotRan` with the reason. Connection errors,
  timeouts, 5xx and 429 are retried with backoff; other errors are final.
- **Scope.** Single-turn HTTP with JSON in and out. Streaming-only endpoints, multi-turn
  sessions and other transports would need a small adapter in code.

## Judging with an LLM

Five check types ask a judge model for a verdict: **Correctness** and **Hallucination**
compare the answer with the expected one; **Relevance**, **Bias** and **Toxicity** judge
the answer on its own. Each has a default rubric; an assignment can set its own.

- **Choose the judge** on the Settings page or with `ASSAY_JUDGE_*`: `anthropic` or
  `openai`, a model, and optionally an endpoint (vLLM, Ollama, LM Studio and OpenRouter
  work through `openai`). The key is read from an environment variable, never stored.
- **Check it** with `POST /settings/judge/checks`: one small question, and either a
  verdict or the exact reason it failed.
- **What it returns:** `passed` and a short rationale (the result's `detail`), at
  temperature 0. Each result records the rubric and the judge model it was graded with.
- **When the judge can't answer**, that check fails with the reason; the run's other
  checks are scored as usual.
- **Cost:** each judge check is one paid call.

## Running with statistics

A GenAI application answers differently each time. To claim "this check passes at least
9 times in 10", run the same test, set or plan **N times as one batch** and let a
statistical test decide, with a stated confidence — or say honestly that there aren't
enough runs yet.

1. **Pick a question** from `GET /statistics/tests`: *passes reliably* (binomial gate),
   *scores high enough on average* (one-sample t-test), or *the judge is consistent*.
2. **See the cost first** with `POST /statistics/estimate`: the fewest runs that can
   conclude anything, the sizes worth choosing, the calls they'll make, and each check's
   outlook from its history. It creates nothing.
3. **Run it** with `POST /statistics/batches`, or let it run *until there's an answer*:
   in waves, stopping as soon as every check is decided.
4. **Read the result** on `GET /statistics/batches/{id}`: a verdict per check — `pass`,
   `fail` or `inconclusive` — with its interval and the per-run series for charts.
5. **Compare two batches** with `POST /statistics/comparisons`: pass rates, *no worse
   than* by a margin, mean scores, score ranks, or paired by entry.

Every batch run is a full, paid run, so statistics are always a deliberate choice.

## API

The interactive reference, with an example for every response, is at `/docs`; the
OpenAPI description at `/openapi.json`.

**Lists** share one set of conventions:

- `offset` and `limit` (1–500); `total` counts everything within the filters.
- A filter given several times means any of its values (`?status=Red&status=NotRan`);
  different filters narrow together.
- `q` searches, ignoring case; give it once per phrase, and every phrase must be found.
- `created_from` / `created_to` take ISO 8601 moments. Every timestamp is stored and sent
  in UTC (`2026-09-27T12:36:59.928077Z`).
- Each main list has a `/facets` sibling counting the items by each filter's values.

**Errors** use FastAPI's `{"detail": …}`; a 500 also returns a `request_id` to quote.

| Area | Endpoints |
|---|---|
| Health | `GET /health`, `GET /health/worker` (broker and workers: ready, outdated, unreachable) |
| Datasets | `GET`/`DELETE /datasets`, `GET /datasets/facets`, `GET /datasets/{id}`, `GET /datasets/{id}/rows`, `POST /datasets/path` (import a `.jsonl`), `POST`/`PUT`/`PATCH`/`DELETE /datasets/rows`, `PATCH /datasets/name` |
| Tests | `GET /tests/types`, `GET`/`POST`/`DELETE /tests`, `GET /tests/facets`, `GET`/`PATCH /tests/{id}`, `GET /tests/{id}/test-sets`, `POST /tests/from-dataset` |
| Test sets | `GET`/`POST /test-sets`, `GET /test-sets/facets`, `GET`/`PATCH`/`DELETE /test-sets/{id}`, `GET /test-sets/{id}/test-plans`, entries: `GET`/`POST`/`PATCH`/`DELETE /test-sets/{id}/entries`, `GET`/`PATCH /test-sets/{id}/entries/{entry_id}` |
| Test plans | `GET`/`POST /test-plans`, `GET /test-plans/facets`, `GET`/`PATCH`/`DELETE /test-plans/{id}`, `GET`/`POST`/`DELETE /test-plans/{id}/entries` |
| Runs | `POST /runs/standalone/{test_id}`, `POST /runs/test-sets/{id}` (live), `POST /runs/test-sets/{id}/executions/{execution_id}` (replay), the same two for `/runs/test-plans/{id}`, `GET /runs`, `GET /runs/facets`, `GET /runs/executions`, and each scope's executions, runs and run details |
| Settings | `GET`/`PATCH`/`DELETE /settings/target` and `/settings/judge`, `POST /settings/{target,judge}/checks`, `GET /settings/{target,judge}/checks/{id}` |
| Statistics | `GET /statistics/tests`, `POST /statistics/estimate`, `GET`/`POST /statistics/batches`, `GET /statistics/batches/facets`, `GET /statistics/batches/{id}`, `POST /statistics/batches/{id}/stop`, `GET`/`POST /statistics/comparisons`, `GET /statistics/comparisons/facets`, `GET /statistics/comparisons/{id}` |

What is protected, and why:

- An entry that has run can't be edited or deleted; a test set whose entries have run
  can't be deleted; a test plan that has ever run can't be deleted.
- Unlinking an entry from its set, or a set from a plan, is always allowed: runs keep
  pointing at the frozen entries.
- Deleting dataset rows never touches tests made from them; the tests just lose their
  link to the row.

## Worker

`assay.worker` is a separate Celery process: it picks up each run the API creates, scores
it and writes the outcome back. It needs the `worker` extra and Redis.

```bash
pip install -e ".[worker]"            # add ".[worker,nlp]" for the NLP metrics
celery -A assay.worker worker --loglevel=info --pool=threads
celery -A assay.worker beat --loglevel=info      # exactly one per environment
```

- **Beat** re-sends runs whose dispatch was lost, and resumes batches that stalled.
- **NLP metrics** need the `nlp` extra (PyTorch, about 1 GB); BERTScore and Cosine
  Similarity download their models (about 800 MB) on first use. METEOR also needs
  WordNet: `python -m nltk.downloader wordnet`. A missing piece fails only its checks.
- **`--pool=threads`** avoids a Celery prefork issue on macOS and newer Pythons; size
  `ASSAY_WORKER_DB_POOL_SIZE` to the worker's concurrency.
- **`GET /health/worker`** says whether runs sent now will be executed, and whether a
  worker runs older code than the API (restart it then).

| Broker | Broker / result backend URL |
|---|---|
| Local Redis (default) | `redis://localhost:6379/0` |
| Local, nothing to install | `sqla+sqlite:///./celery_broker.sqlite` / `db+sqlite:///./celery_results.sqlite` |
| Managed Redis with TLS | `rediss://:<key>@<host>:6380/0` (certificates verified) |

**Docker.** `Dockerfile` builds the API; `Dockerfile.worker` builds the worker, with the
NLP extras and WordNet included. Beat runs from the worker image with another command:

```bash
docker build -f Dockerfile.worker -t assay-worker .
docker run --env-file .env assay-worker
docker run --env-file .env assay-worker celery -A assay.worker beat --loglevel=info
```

**Flower** (in the `dev` extra) shows the worker's task queue:
`celery -A assay.worker flower --port=5555`.

## Observability

- **Metrics:** `GET /metrics`, in Prometheus format: request counts and latency by method,
  status and route.
- **Logs** go to stdout, one record per line, `text` or `json`. Every request gets an ID
  (`X-Request-ID`, yours reused when you send one) that's on every log line it causes and
  on the response; a run's worker lines carry the same ID and the run's ID.
- **One access line per request** (method, route, status, duration), plus one event per
  state change. Never a test's input or output, nor a dataset row.

## Database

`ASSAY_DATABASE_URL` picks it: SQLite locally, PostgreSQL in production
(`postgresql+asyncpg://user:pass@host:5432/assay`), with no code changes.

```bash
alembic upgrade head                                  # apply migrations
alembic revision --autogenerate -m "describe it"      # create one
alembic downgrade -1                                  # undo the last one
```

## Development

```bash
pytest                    # the suite
ruff check src/ tests/    # lint
pytest --cov=assay --cov-report=term-missing
```

Some tests step aside by design when an optional dependency is missing:

| Tests | Shown as | Needs | Run them with |
|---|---|---|---|
| METEOR scoring | skipped | NLTK's WordNet | `python -m nltk.downloader wordnet` |
| BERTScore scoring | skipped | the `nlp` extra | `pip install -e ".[dev,nlp]"` |
| BERTScore and Cosine Similarity on real models | deselected | the `nlp` extra, ~800 MB of models | `pytest -m slow` |

**Layout:** `src/assay/` holds the API (`api/` routes, `schemas/`, `services/`
business logic, `models/` tables), the worker (`worker/`: tasks, evaluators, the
application and judge clients) and the code both share (settings, timestamps, the
statistics). `alembic/` holds the migrations, `tests/` the suite.
