import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.engine import make_url

from assay import __version__
from assay.api import router
from assay.config import settings
from assay.exception_handlers import register_exception_handlers
from assay.logging_config import configure_logging
from assay.middleware import REQUEST_ID_HEADER, RequestContextMiddleware

logger = logging.getLogger(__name__)

_DESCRIPTION = """
Assay measures how a GenAI-powered application actually behaves, and keeps a reproducible
record of every measurement.

**What it does.** You bring the prompts you send your application, the answers it gave
if you recorded them, and — where you have them — the outputs you expected. When a test
has no recorded answer, Assay asks your application itself, through its endpoint, and
scores the reply. Assay scores each case with the checks you assign to it:

- **Deterministic** checks, pass/fail — *Exact Match*, *Contains*, *Regex Match* and
  their variants (case-insensitive, whitespace-sensitive, full match), the negated
  *Does Not Contain* and *Regex Must Not Match*, the JSON checks (*Is Valid JSON*,
  *Matches JSON Schema*, *JSON Field Equals*) and length limits (*Word Count Limit*,
  *Character Count Limit*).
- **NLP metrics** scored against a threshold you set — *ROUGE* (and ROUGE-1, ROUGE-2,
  ROUGE-L Recall, ROUGE-L Precision), *BLEU*, *METEOR*, *BERTScore*, *Cosine Similarity*.
- **LLM-as-judge** verdicts against a rubric you write — *Correctness*, *Relevance*,
  *Bias*, *Toxicity*, *Hallucination*.

Every check yields a pass/fail — with a score when it measures something on a scale, or a
rationale — and every run rolls up to a single outcome. A check reads the answer by
default, or any other part of your application's reply you point it at (`answer_path`).

**How the pieces fit.**

- **Datasets** — rows of `prompt` / `expected_output` / `model_output`, uploaded from a
  `.jsonl` file. The raw material; rows can be added, replaced, edited and deleted.
- **Tests** — one case each: an input, its outputs, and the checks (*test types*) to run
  on it. Created by hand or in bulk from a dataset's rows. A test is always editable.
- **Test sets** — a named baseline. Adding a test to a set snapshots it, so the set stays
  stable while the live tests keep evolving. An entry can still be edited until its first
  run, then it freezes for good — a run's record of what it executed never changes.
- **Test plans** — campaigns that group test sets. Membership can change at any time.
- **Runs and executions** — a *standalone* run of one test, or an *execution* of a whole
  set or plan, one run per entry. A **live** execution covers the current membership; a
  **replay** re-runs exactly what a past execution ran. A run goes `Pending` → `Running`
  → `Green` (every check passed), `Amber` (mixed), `Red` (every check failed) or `NotRan`
  (nothing could be attempted), with the result of each check kept alongside. Every run
  keeps a record of the test exactly as it was run, so editing a test later never changes
  a past run's history.
- **Statistical verification** — a one-sample z-test over a series of metric scores
  against a threshold, for claims like "this metric holds above 0.8 at α = 0.05".
- **Settings** — how Assay calls your application (URL, headers, request body, where the
  answer is in the reply), saved from the UI; and *checks* that make one call through a
  worker to confirm the settings work before any run depends on them.

**Conventions.** List endpoints paginate with `offset`/`limit` and always return `total`.
Every response carries an `X-Request-ID` header — send your own to have it reused — and a
500 also returns it in the body: quote it when reporting a problem. Errors use
`{"detail": ...}`.
"""


def create_app() -> FastAPI:
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Assay",
        summary="Test how a GenAI application behaves — and keep a reproducible record of it.",
        description=_DESCRIPTION,
        version=__version__,
        responses={
            500: {
                "description": (
                    "Unexpected server error. `request_id` matches the response's "
                    "`X-Request-ID` header and the server's logs for this request — "
                    "quote it when reporting the problem."
                ),
                "content": {
                    "application/json": {
                        "example": {
                            "detail": "Internal server error",
                            "request_id": "3f2b9c6e4d0a4c1e8b7a6d5c4b3a2f10",
                        }
                    }
                },
            },
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        # browsers reject credentialed requests against a wildcard origin
        allow_credentials=settings.cors_allowed_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        # without this a browser client can't read the header on a cross-origin response
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Added after CORS so it wraps it: preflight responses get an ID and an
    # access line too, and the request ID is set before anything else runs.
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(router)
    Instrumentator().instrument(app).expose(app)

    logger.info(
        "Assay %s starting: log_level=%s log_format=%s database=%s",
        __version__,
        settings.log_level,
        settings.log_format,
        make_url(settings.database_url).render_as_string(hide_password=True),
        extra={"version": __version__, "log_level": settings.log_level},
    )
    return app


app = create_app()


if __name__ == "__main__": # pragma: no cover
    import uvicorn

    uvicorn.run(
        "assay.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
