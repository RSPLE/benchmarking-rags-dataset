# Benchmarking RAGs Dataset

> **Current execution:** pipelines use v2 checkpoints and isolated outputs under
> `resultados/<rag>/<experiment_id>`. Historical files in `app/rags/*/results/` remain
> unchanged. See the [reliability and operations guide](app/docs/reliability.md)
> before starting new batches. That guide supersedes the historical instructions
> below about resuming, output directories, ingestion and evaluation settings.

[Versão em português](README.pt-BR.md)

**Web interface:** see [dashboard first login](app/dashboard/README.md) to start
by setting `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD` and `DASHBOARD_PUBLIC_HOST` in `.env`. Then run
`docker compose up -d`. On the VPS, open `https://<DASHBOARD_PUBLIC_HOST>`; locally,
<http://127.0.0.1:8501>. The [Caddyfile](Caddyfile) publishes the dashboard over HTTPS.
The sidebar provides execution/resume and pending cases. All services start without
profiles and application images build locally. There are no default credentials.

A monorepo for comparing six Retrieval-Augmented Generation (RAG) architectures against one shared dataset of 90 questions and reference answers. It measures quality with RAGAS, latency, and token usage; uses Chroma for vector indexing; and supports OpenRouter as the default LLM and embedding provider. Every pipeline has its own `uv` environment and lockfile to prevent dependency conflicts.

## Repository contents

| Directory | Strategy | Retrieval / enrichment |
|---|---|---|
| `app/rags/context-rag/` | Classic RAG | vector top-5; answer restricted to retrieved context |
| `app/rags/graph-rag/` | Experimental Graph RAG | Chroma + an LLM-extracted NetworkX graph; LangGraph agent |
| `app/rags/hybrid-rag/` | Hybrid RAG | BM25 (0.4 weight) + Chroma (0.6 weight) |
| `app/rags/knowledge-enhanced-rag/` | Knowledge-Enhanced RAG | Chroma + a curated Neo4j learning graph |
| `app/rags/memory-augmented-rag/` | Memory-Augmented RAG | LangGraph agent with `MemorySaver` and a retrieval tool |
| `app/rags/self-rag/` | Simplified Self-RAG | generation, binary self-critique, and at most one refinement |
| `data/evaluation/` | Shared dataset | 90 items (`Q001`–`Q090`) with question, answer, and source |

The original implementations and notebooks remain available for inspection. Installation, model-provider configuration, dataset selection, and execution policy are managed from the repository root.

## Sequential, resumable execution

New runs write to `resultados/<rag>/<experiment_id>`. The v2 checkpoint persists
answers and evidence before judging, then each metric separately. Resuming a
compatible experiment reuses completed stages. Changed code, configuration,
corpus or dataset creates another identity. Historical outputs remain unchanged
under `app/rags/<rag>/results/` and v1 checkpoints are not resumed automatically.

`--questions` limits attempts per RAG, not successful answers. Failed cases take
priority by default; use `--selection pending` for new cases. Configuration,
credit and budget errors pause the batch, as do repeated equivalent failures.
Multiple-pipeline execution stops on the first failing process.

`results.csv` preserves the nine original RSPLE columns, semicolon delimiter and UTF-8 BOM.
`<rag>-run-<repetition>_1.csv` is an identical copy for existing plotting scripts;
`results_detailed.csv` contains extra fields. Only complete cases enter the primary
CSV. See the [compatibility audit](app/docs/compatibility.md).

`errors.json` describes current failures,
`events.jsonl` preserves history, and `summary.json` includes coverage and metric
denominators. Atomic writes and process locks protect local state.

See the [operations guide](app/docs/reliability.md) for migration, budgets, pause,
frozen-answer evaluation and Telegram control.
The [deployment guide](app/deployment/README.md) covers the local socket, single `.env`,
authorized IDs and durable queue. The [acceptance matrix](app/docs/acceptance.md)
separates offline checks from the live pilot.


## Terminal and Telegram commands

```bash
uv run --locked python -m app telegram-check
uv run --locked python -m app run all --questions 1 --max-calls 200 --max-seconds 3600
uv run --locked python -m app resume context-rag EXP --questions 3
```

```text
/start
/executar all --questions 1 --max-calls 200 --max-seconds 3600
/retomar context-rag EXP --questions 3
/status
```

`EXP` is the complete ID shown by `/status`. `/start` shows help in the private
bot conversation when services are running. The channel receives results. Shared
flags and comparison limits are in the [compatibility protocol](app/docs/compatibility.md).
Knowledge shares the last five question/answer pairs across the 90 questions,
including after a resumed process.

## Requirements and uv installation

- Python 3.11, 3.12, or 3.13 for local execution;
- [`uv`](https://docs.astral.sh/uv/);
- Docker Desktop with Docker Compose only if you want to run Neo4j locally in a container;
- an OpenRouter or OpenAI API key;
- Neo4j only for `required` graph mode; `snapshot` mode does not need a database;
- locally supplied PDFs that you are authorized to use.

```bash
git clone https://github.com/RSPLE/benchmarking-rags-dataset.git
cd benchmarking-rags-dataset
uv sync
cp -n .env.example .env
```

PowerShell equivalent for the final command:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

The root `pyproject.toml` and `uv.lock` contain only orchestration dependencies. Each directory under `app/rags/` has its own `pyproject.toml`, `uv.lock`, and on-demand `.venv`. There are no `requirements.txt` files, so one pipeline can evolve its dependency set without changing another pipeline's environment.

All operational settings belong in the **root `.env`**, including models, Neo4j
and Telegram. See [where to enter the private channel and bot settings](app/docs/configuration.md).
There is no mandatory dollar cap. Knowledge uses the same seven PDFs and Neo4j;
the additional JSON snapshot is inactive. Preserve existing `.env` values when upgrading.

## OpenRouter

OpenRouter is the default for chat and embeddings through its OpenAI-compatible `https://openrouter.ai/api/v1` endpoint. See the official [quickstart](https://openrouter.ai/docs/quickstart) and [Embeddings API](https://openrouter.ai/docs/api/reference/embeddings).

Minimal configuration:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=<EXACT_CONFIGURED_MODEL_ID>
LLM_MAX_TOKENS=1024
RAGAS_MAX_TOKENS=2048
LLM_TIMEOUT_SECONDS=600
RAGAS_TIMEOUT_SECONDS=600
RAGAS_MAX_WORKERS=1
RAGAS_MAX_ATTEMPTS=1
OPENROUTER_JUDGE_MODEL=
LLM_MAX_TOKENS=4096

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
app/rags/context-rag/docs/
app/rags/graph-rag/docs/
app/rags/hybrid-rag/docs/
app/rags/knowledge-enhanced-rag/data/apostilas/
app/rags/memory-augmented-rag/docs/
app/rags/self-rag/docs/
```

Each pipeline creates its own Chroma index on first run. Indexes, results, secrets, and virtual environments are ignored by Git.

## Optional local Neo4j

Benchmarks can be controlled through the CLI or, with services running, through the private Telegram bot conversation. Docker is not required for the five RAGs that do not use Neo4j. To run `knowledge-enhanced-rag` with the local Neo4j service included in Compose, update `.env`:

```env
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=a-secure-local-password
```

Start only the database:

```bash
docker compose up --detach neo4j
```

The database is available to the CLI at `127.0.0.1:7687`. The `neo4j-data` volume retains its data and the password used on first initialization; if it already exists, keep that password in `.env`. To follow or stop the database service:

```bash
docker compose logs -f neo4j
docker compose down
```

Recommended order for testing `knowledge-enhanced-rag` with local Neo4j:

```bash
docker compose up --detach neo4j
uv run python -m app doctor
uv run python -m app run knowledge-enhanced-rag --questions 3
```

### Hosted Neo4j

To use Neo4j Aura or another hosted instance, do not start the local `neo4j` service. The dashboard can still run in Docker. Create the hosted database, copy the credentials provided by the service, and configure `.env`:

```env
NEO4J_URI=neo4j+s://your-id.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
```

Then run the benchmark directly:

```bash
uv run python -m app run knowledge-enhanced-rag --questions 3
```

`doctor` checks local settings and files; it does not prove Neo4j connectivity.
The benchmark does not populate the database. With `BENCHMARK_KG_MODE=required`,
missing or empty Neo4j data stops execution; there is no silent vector-only fallback.
See [graph preparation and rebuild controls](app/docs/reliability.md#knowledge-and-neo4j)
before using the historical API's graph-building endpoint. Explicit `disabled`
mode creates another experiment and is not the original Knowledge protocol.

## Usage

## Step-by-step execution

1. Place the PDFs directly in each RAG's `docs/` directory. For `knowledge-enhanced-rag`, use `data/apostilas/`.
2. Configure the key and model in `.env`.
3. Run the script. It validates the PDFs, runs `uv sync`, checks the configuration, and starts all six pipelines:

```bash
bash app/scripts/run_benchmark.sh
```

The script tests one question per pipeline by default. To test another batch size:

```bash
QUESTIONS=10 bash app/scripts/run_benchmark.sh
```

Ingestion happens automatically while each pipeline starts. The pipeline loads the PDFs, splits the text into chunks, generates embeddings, and writes the Chroma index before processing questions.

To test each RAG individually with up to three questions:

```bash
uv run python -m app run context-rag --questions 3
uv run python -m app run graph-rag --questions 3
uv run python -m app run hybrid-rag --questions 3
uv run python -m app run knowledge-enhanced-rag --questions 3
uv run python -m app run memory-augmented-rag --questions 3
uv run python -m app run self-rag --questions 3
```

Before testing, check the configuration and PDF counts:

```bash
uv sync
uv run python -m app doctor
```

`uv sync` installs dependencies but does not ingest documents. Ingestion happens when each RAG starts. Each RAG has its own Chroma index and stores new results in `resultados/<pipeline>/<experiment_id>/`.

```bash
# List the available pipelines
uv run python -m app list

# Check provider keys and local PDF counts
uv run python -m app doctor

# Run or resume one RAG
uv run python -m app run self-rag

# Run only the next 10 unresolved questions
uv run python -m app run self-rag --questions 10

# Run two or more RAGs in the requested order
uv run python -m app run context-rag hybrid-rag self-rag

# Run a batch of 15 questions in each selected RAG
uv run python -m app run context-rag hybrid-rag self-rag --questions 15

# Run or resume all six RAGs sequentially in isolated environments
uv run python -m app run all

# Run the next 5 questions in each of the six RAGs
uv run python -m app run all --questions 5

# Override the LLM provider for one run
uv run python -m app run hybrid-rag --provider openai

```

The previous `uv run python -m app run-all` command remains available as an alias.
`--limit` is an alias for `--questions`. The limit applies independently to every selected RAG; it is not divided among them.

A successful batch exits with code `0`, even when the selected limit leaves pending questions. The command exits with code `1` when one or more questions attempted in that run fail. This makes partial successful batches safe to use in CI scripts.

## Recommended execution flows

Process the dataset in batches of 10:

```bash
uv run python -m app run self-rag --questions 10
# Repeat until the summary reports 90 successes and no pending questions.
```

Resume after a failure, cancellation, or restart by running the same command:

```bash
uv run python -m app run self-rag --questions 10
```

No offset is required. The checkpoint skips successes, retries failures first, and continues from the next pending item. Do not edit only the CSV to change progress; `checkpoint.json` holds the authoritative state.

For valid comparisons, keep the generation model, embedding model, documents, and remaining `.env` settings equal across pipelines. `--provider` overrides the LLM provider for that invocation only; embeddings remain controlled by `EMBEDDING_PROVIDER`.

## Dataset and metrics

`data/evaluation/qa_dataset_90.json` has 90 objects with a stable `id`, `question`, `ground_truth`, and `source_book`. Each question is evaluated for `faithfulness`, `answer_relevancy`, `context_precision`, and `context_recall`. The CSV also records response time and input, output, and total token counts.

## Layout

```text
.
├── main.py                  # monorepo CLI
├── app/benchmark/runner.py      # per-question checkpoints and resume logic
├── app/providers/models.py          # shared OpenRouter/OpenAI configuration
├── docker-compose.yml       # optional local Neo4j
├── pyproject.toml
├── uv.lock                 # orchestrator dependencies only
├── .env.example
├── data/evaluation/
└── app/rags/
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

## Common problems

- **Incompatible checkpoint:** preserve the file and inspect its manifest. Use explicit migration for v1 archives; changed methodology requires a new experiment.
- **Missing API key:** run `uv run python -m app doctor` and inspect the root `.env`, which is shared by all six RAGs.
- **Neo4j in Docker:** because benchmarks run on the host through the CLI, use `NEO4J_URI=bolt://127.0.0.1:7687` in `.env`.
- **Neo4j Aura:** use the instance-provided `neo4j+s://...` URI and matching credentials. Do not mix the password of the local persistent database with Aura credentials.
- **Changed embedding model:** choose another `CHROMA_PERSIST_DIR` or deliberately rebuild the index; different vector dimensions must not share a collection.

## Development checks

```bash
uv run python -m unittest discover -s app/tests -v
uv run ruff check main.py app/benchmark/runner.py tests
```

The suite covers one/many/all RAG selection, limit validation, incremental runs without duplicate rows, failure-first resumption, and removal of recovered entries from `errors.json`.

## License
