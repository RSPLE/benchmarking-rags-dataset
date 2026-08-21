FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG RAG_NAME
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/rag-venv \
    RAG_NAME=${RAG_NAME}

WORKDIR /workspace

COPY rags/${RAG_NAME}/pyproject.toml rags/${RAG_NAME}/uv.lock /workspace/rags/${RAG_NAME}/
RUN uv sync --project /workspace/rags/${RAG_NAME} --locked --no-dev

COPY benchmark_runner.py rag_provider.py ragas_compat.py /workspace/
COPY eval-dataset /workspace/eval-dataset
COPY apps/runner /workspace/apps/runner
COPY rags/${RAG_NAME} /workspace/rags/${RAG_NAME}

EXPOSE 8090
CMD ["python", "/workspace/apps/runner/server.py"]
