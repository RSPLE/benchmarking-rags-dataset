# Benchmarking RAGs Dataset

[Versão em português](README.md)

A monorepo for comparing six Retrieval-Augmented Generation (RAG) architectures against one shared dataset of 90 questions and reference answers. It measures quality with RAGAS, latency, and token usage; uses Chroma for vector indexing; and supports OpenRouter as the default LLM and embedding provider. Every pipeline has its own `uv` environment and lockfile to prevent dependency conflicts.

## Repository contents

| Directory | Strategy | Retrieval / enrichment |
|---|---|---|
| `rags/context-rag/` | Classic RAG | vector top-5; answer restricted to retrieved context |
| `rags/graph-rag/` | Experimental Graph RAG | Chroma + an LLM-extracted NetworkX graph; LangGraph agent |
| `rags/hybrid-rag/` | Hybrid RAG | BM25 (0.4 weight) + Chroma (0.6 weight) |
| `rags/knowledge-enhanced-rag/` | Knowledge-Enhanced RAG | Chroma + a curated Neo4j learning graph; includes a FastAPI API |
| `rags/memory-augmented-rag/` | Memory-Augmented RAG | LangGraph agent with `MemorySaver` and a retrieval tool |
| `rags/self-rag/` | Simplified Self-RAG | generation, binary self-critique, and at most one refinement |
| `eval-dataset/` | Shared dataset | 90 items (`Q001`–`Q090`) with question, answer, and source |
| `apps/ui/` | React dashboard | execution, logs, progress, and visual comparison |
| `apps/api/` | Dashboard API | runner, checkpoint, and CSV aggregation |

The original implementations and notebooks remain available for inspection. Installation, model-provider configuration, dataset selection, and execution policy are managed from the repository root.

## Sequential, resumable execution

Every pipeline processes the dataset once, one question at a time. After every attempt, the runner atomically updates:

- `rags/<pipeline>/results/checkpoint.json`: status, attempts, errors, and result for each ID when running locally;
- `rags/<pipeline>/results/results.csv`: successful questions with generated answer, RAGAS metrics, latency, and tokens.

On the next run, successful items are skipped. Failed, interrupted (`running`), and pending items are retried. The checkpoint also stores the dataset SHA-256 and refuses to combine results if the dataset changes.

For example, if only `Q037` fails in Self-RAG, running the same command again processes `Q037` without paying for the 89 successful questions again.

## Requirements and uv installation

- Python 3.11, 3.12, or 3.13 for local execution;
- [`uv`](https://docs.astral.sh/uv/);
- Docker Desktop with Docker Compose for the containerized dashboard;
- an OpenRouter or OpenAI API key;
- Neo4j only for the Knowledge-Enhanced RAG graph;
- locally supplied PDFs that you are authorized to use.

```bash
git clone https://github.com/RSPLE/benchmarking-rags-dataset.git
cd benchmarking-rags-dataset
uv sync
cp .env.example .env
```

PowerShell equivalent for the final command:

```powershell
Copy-Item .env.example .env
```

The root `pyproject.toml` and `uv.lock` contain only orchestration dependencies. Each directory under `rags/` has its own `pyproject.toml`, `uv.lock`, and on-demand `.venv`; the API also has an isolated `apps/api/pyproject.toml` and `uv.lock`. There are no `requirements.txt` files, so one pipeline can evolve its dependency set without changing another pipeline's environment.

## OpenRouter

OpenRouter is the default for chat and embeddings through its OpenAI-compatible `https://openrouter.ai/api/v1` endpoint. See the official [quickstart](https://openrouter.ai/docs/quickstart) and [Embeddings API](https://openrouter.ai/docs/api/reference/embeddings).

Minimal configuration:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=~openai/gpt-latest

EMBEDDING_PROVIDER=openrouter
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
```

The providers are independent. To generate through OpenRouter while creating embeddings directly through OpenAI:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...

EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-large
```

Set both providers to `openai` to bypass OpenRouter. When changing embedding models, choose a new `CHROMA_PERSIST_DIR` or deliberately remove the previous index; vector dimensions from different models cannot share a collection.

## Local corpora

PDF files are not distributed by this monorepo. This keeps the Git repository manageable and avoids redistributing works without verified permission. Place documents you are entitled to use under:

```text
rags/context-rag/docs/
rags/graph-rag/docs/
rags/hybrid-rag/docs/
rags/knowledge-enhanced-rag/data/apostilas/
rags/memory-augmented-rag/docs/
rags/self-rag/docs/
```

Each pipeline creates its own Chroma index on first run. Indexes, results, secrets, and virtual environments are ignored by Git.

## Dashboard and containers

The dashboard uses a minimal operational interface inspired by the Smart project. The **Pipelines** page provides one job per RAG with run/resume controls, per-question progress, failure counts, and logs. The **Results** page compares RAGAS averages and downloads each pipeline's CSV.

Prepare the root `.env` and local corpora, then start the complete stack:

```bash
uv run python main.py dashboard --detach
```

This is equivalent to `docker compose up --build --detach`. After the initial build:

- dashboard: `http://localhost:8080`;
- dashboard API documentation: `http://localhost:8001/docs`;
- local Neo4j Browser: `http://localhost:7474`.

Each RAG has a separate image and runner. Python dependencies stay inside containers; only Docker, the PDFs, and the shared root `.env` are required on the host. Checkpoints and CSVs live in `benchmark-results`, Chroma indexes in `rag-cache`, and the local graph in `neo4j-data`. All three volumes survive `docker compose down`; CSVs can be downloaded from the UI.

```bash
docker compose logs -f
docker compose down
```

## Usage

```bash
# List the available pipelines
uv run python main.py list

# Check provider keys and local PDF counts
uv run python main.py doctor

# Run or resume one RAG
uv run python main.py run self-rag

# Run two or more RAGs in the requested order
uv run python main.py run context-rag hybrid-rag self-rag

# Run or resume all six RAGs sequentially in isolated environments
uv run python main.py run all

# Override the LLM provider for one run
uv run python main.py run hybrid-rag --provider openai

# Start the Knowledge-Enhanced RAG API
uv run python main.py api --host 127.0.0.1 --port 8000
```

The previous `uv run python main.py run-all` command remains available as an alias.

FastAPI documentation is then available at `http://127.0.0.1:8000/docs`.

## Dataset and metrics

`eval-dataset/qa_dataset_90.json` has 90 objects with a stable `id`, `question`, `ground_truth`, and `source_book`. Each question is evaluated for `faithfulness`, `answer_relevancy`, `context_precision`, and `context_recall`. The CSV also records response time and input, output, and total token counts.

## Layout

```text
.
├── main.py                  # monorepo CLI
├── benchmark_runner.py      # per-question checkpoints and resume logic
├── rag_provider.py          # shared OpenRouter/OpenAI configuration
├── docker-compose.yml       # UI, API, six runners, and Neo4j
├── docker/                  # API and runner images
├── pyproject.toml
├── uv.lock                 # orchestrator dependencies only
├── .env.example
├── eval-dataset/
├── apps/
│   ├── api/                 # FastAPI + uv
│   ├── runner/              # internal container HTTP service
│   └── ui/                  # React + Vite + nginx
└── rags/
    ├── context-rag/
    ├── graph-rag/
    ├── hybrid-rag/
    ├── knowledge-enhanced-rag/
    ├── memory-augmented-rag/
    └── self-rag/
```

Each pipeline directory also contains its own `pyproject.toml` and `uv.lock`. `/.env` is the only secrets configuration file; subdirectories do not keep duplicate `.env.example` files.

## Methodology notes

- This repository's Graph RAG is not Microsoft's GraphRAG implementation; it uses a smaller NetworkX graph extracted from a chunk sample.
- Self-RAG is a prompting-based approximation without trained reflection tokens.
- Memory-Augmented RAG keeps in-process memory; benchmark questions remain isolated for fair comparison.
- Knowledge-Enhanced RAG uses a curated Neo4j graph; the other pipelines do not require Neo4j.
- Comparisons are meaningful only when models, corpora, and parameters are controlled across pipelines.

## License

No software license has been declared yet. Until a `LICENSE` file is added, all rights to the code remain reserved. Corpus documents retain their original authors' rights and are not part of this monorepo distribution.
