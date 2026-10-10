FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHON_DOTENV_DISABLED=1 MPLCONFIGDIR=/tmp/matplotlib
WORKDIR /app
COPY app/dashboard/pyproject.toml app/dashboard/uv.lock /app/app/dashboard/
RUN uv sync --project app/dashboard --frozen --no-dev --python /usr/local/bin/python
COPY app/*.py /app/app/
COPY app/benchmark /app/app/benchmark
COPY app/plots /app/app/plots
COPY app/providers /app/app/providers
COPY app/services /app/app/services
COPY app/telegram /app/app/telegram
COPY app/rags/context-rag/plot_graph.py /app/app/rags/context-rag/plot_graph.py
COPY app/dashboard /app/app/dashboard
COPY data/evaluation/qa_dataset_90.json /app/data/evaluation/qa_dataset_90.json
RUN groupadd --gid 1000 benchmark && useradd --uid 1000 --gid 1000 --create-home benchmark && mkdir -p /state /backups && chown benchmark:benchmark /state /backups
USER benchmark
ENV PATH=/app/app/dashboard/.venv/bin:$PATH PYTHONPATH=/app
WORKDIR /app/app/dashboard
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"
CMD ["python", "-m", "app.dashboard.server", "--address", "0.0.0.0"]
