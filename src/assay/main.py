from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from assay import __version__
from assay.api import router
from assay.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="Assay",
        description=(
            "Evaluation toolkit for GenAI-powered applications: "
            "NLP metrics, LLM-as-judge, and statistical reporting."
        ),
        version=__version__,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        # browsers reject credentialed requests against a wildcard origin
        allow_credentials=settings.cors_allowed_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    Instrumentator().instrument(app).expose(app)
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
