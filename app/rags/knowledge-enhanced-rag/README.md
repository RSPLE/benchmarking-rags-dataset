# Knowledge-Enhanced RAG

[Português](README.pt-BR.md) · [Repository](../../../README.md) · [Deployment](../../deployment/README.md)

Top-5 PDF retrieval with 800/100 chunking, a curated Neo4j concept graph and the original generation prompt. A shared session retains the last five question/answer pairs; checkpoints restore that history without regenerating saved answers.

The default `BENCHMARK_KG_MODE=required` needs a populated Neo4j database. The
graph supplements the same seven PDFs used by the other RAGs; it is not another
PDF corpus. The optional `data/knowledge-graph.json` snapshot is inactive unless
explicitly selected. Graph rebuilding is disabled by default. See the deployment
guide before preparing a new database. `api.py` retains the standalone research
API; the supported public interface is the authenticated dashboard.

## Run from the repository root

Use the single root `.env` and the shared 90-question dataset in
`data/evaluation/qa_dataset_90.json`. Compose mounts the same seven PDFs at
`/corpus` for all six RAGs. Local execution uses this project's corpus directory
unless `DOCS_DIR` is set to a shared directory in the root `.env`.

```bash
docker compose exec control python -m app run knowledge-enhanced-rag --questions 1
```

This starts actual model work, including preparation when required. For local
execution outside Docker, use the same CLI with the project's locked environment:

```bash
uv sync --locked --python 3.12
uv run --locked python -m app run knowledge-enhanced-rag --questions 1
```

The CLI selects `app/rags/knowledge-enhanced-rag/pyproject.toml` and `uv.lock` automatically.
Keep the six environments separate. Do not install all RAG dependencies together.

## Results and resume

Results go to `resultados/knowledge-enhanced-rag/<experiment_id>/`. The main `results.csv`
retains the original nine columns, semicolon delimiter and UTF-8 BOM.
Detailed evidence, token accounting, errors and per-metric checkpoints are separate.
The original-name CSV is an identical export, not another repetition.
Historical `results/` files and notebooks remain research records.

```bash
docker compose exec control python -m app resume knowledge-enhanced-rag EXPERIMENT_ID --questions 3
```

Use the full experiment ID and the same models, corpus, dataset and protocol.
The [compatibility protocol](../../docs/compatibility.md) records original revisions,
prompts, retrieval parameters and known differences. The
[layout guide](../../docs/layout.md) describes migration of existing experiments.

## Documentation

- [CLI, Telegram and scientific protocol](../../docs/compatibility.md)
- [Configuration and credentials](../../docs/configuration.md)
- [Checkpoints, accounting and recovery](../../docs/reliability.md)
- [Dashboard and PNG/EPS exports](../../dashboard/README.md)
