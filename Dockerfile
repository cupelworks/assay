FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN uv sync --no-dev --system

# This image is only used for deployments, where a log collector reads stdout —
# JSON makes every field (request_id, status_code, ...) filterable there.
# Locally the app runs via uvicorn directly and keeps the `text` default.
# An App Setting / env var on the host still overrides this.
ENV ASSAY_LOG_FORMAT=json

EXPOSE 8000

CMD ["uvicorn", "assay.main:app", "--host", "0.0.0.0", "--port", "8000"]
