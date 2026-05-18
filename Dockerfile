FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN uv sync --no-dev --system

EXPOSE 8000

CMD ["uvicorn", "assay.main:app", "--host", "0.0.0.0", "--port", "8000"]
