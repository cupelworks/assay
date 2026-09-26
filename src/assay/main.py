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


def create_app() -> FastAPI:
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Assay",
        description=(
            "Evaluation toolkit for GenAI-powered applications: "
            "NLP metrics, LLM-as-judge, and statistical reporting."
        ),
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
