from fastapi import FastAPI
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
    app.include_router(router)
    Instrumentator().instrument(app).expose(app)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "assay.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
