FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/api-venv \
    PATH="/opt/api-venv/bin:$PATH"

WORKDIR /workspace
COPY apps/api/pyproject.toml apps/api/uv.lock /workspace/apps/api/
RUN uv sync --project /workspace/apps/api --locked --no-dev

COPY apps/api /workspace/apps/api
COPY eval-dataset /workspace/eval-dataset

EXPOSE 8000
CMD ["uvicorn", "--app-dir", "/workspace/apps/api", "app:app", "--host", "0.0.0.0", "--port", "8000"]
