# Benchmarking RAGs Dataset

[Versão em português](README.md)

A monorepo for comparing six Retrieval-Augmented Generation (RAG) architectures against one shared dataset of 90 questions and reference answers. It measures quality with RAGAS, latency, and token usage; uses Chroma for vector indexing; and supports OpenRouter as the default LLM and embedding provider. Every pipeline has its own `uv` environment and lockfile to prevent dependency conflicts.

## Repository contents

| Directory | Strategy | Retrieval / enrichment |
|---|---|---|
| `context-rag/` | Classic RAG | vector top-5; answer restricted to retrieved context |
| `graph-rag/` | Experimental Graph RAG | Chroma + an LLM-extracted NetworkX graph; LangGraph agent |
| `hybrid-rag/` | Hybrid RAG | BM25 (0.4 weight) + Chroma (0.6 weight) |
| `knowledge-enhanced-rag/` | Knowledge-Enhanced RAG | Chroma + a curated Neo4j learning graph; includes a FastAPI API |
| `memory-augmented-rag/` | Memory-Augmented RAG | LangGraph agent with `MemorySaver` and a retrieval tool |
| `self-rag/` | Simplified Self-RAG | generation, binary self-critique, and at most one refinement |
| `eval-dataset/` | Shared dataset | 90 items (`Q001`–`Q090`) with question, answer, and source |

The original implementations and notebooks remain available for inspection. Installation, model-provider configuration, dataset selection, and execution policy are managed from the repository root.

## Sequential, resumable execution

Every pipeline processes the dataset once, one question at a time. After every attempt, the runner atomically updates:

- `<pipeline>/results/checkpoint.json`: status, attempts, errors, and result for each ID;
- `<pipeline>/results/results.csv`: successful questions with generated answer, RAGAS metrics, latency, and tokens.

On the next run, successful items are skipped. Failed, interrupted (`running`), and pending items are retried. The checkpoint also stores the dataset SHA-256 and refuses to combine results if the dataset changes.

For example, if only `Q037` fails in Self-RAG, running the same command again processes `Q037` without paying for the 89 successful questions again.

## Requirements and uv installation

- Python 3.11, 3.12, or 3.13;
- [`uv`](https://docs.astral.sh/uv/);
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

The root `pyproject.toml` and `uv.lock` contain only orchestration dependencies. Each RAG directory has its own `pyproject.toml`, `uv.lock`, and on-demand `.venv`. There are no `requirements.txt` files, so one pipeline can evolve its dependency set without changing another pipeline's environment.

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
context-rag/docs/
graph-rag/docs/
hybrid-rag/docs/
knowledge-enhanced-rag/data/apostilas/
memory-augmented-rag/docs/
self-rag/docs/
```

Each pipeline creates its own Chroma index on first run. Indexes, results, secrets, and virtual environments are ignored by Git.

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
├── pyproject.toml
├── uv.lock                 # orchestrator dependencies only
├── .env.example
├── eval-dataset/
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
