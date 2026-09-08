# Benchmarking RAGs Dataset

[Versão em português](README.md)

A monorepo for comparing six Retrieval-Augmented Generation (RAG) architectures against one shared dataset of 90 questions and reference answers. It measures quality with RAGAS, latency, and token usage; uses Chroma for vector indexing; and supports OpenRouter as the default LLM and embedding provider. Every pipeline has its own `uv` environment and lockfile to prevent dependency conflicts.

## Repository contents

| Directory | Strategy | Retrieval / enrichment |
|---|---|---|
| `rags/context-rag/` | Classic RAG | vector top-5; answer restricted to retrieved context |
| `rags/graph-rag/` | Experimental Graph RAG | Chroma + an LLM-extracted NetworkX graph; LangGraph agent |
| `rags/hybrid-rag/` | Hybrid RAG | BM25 (0.4 weight) + Chroma (0.6 weight) |
| `rags/knowledge-enhanced-rag/` | Knowledge-Enhanced RAG | Chroma + a curated Neo4j learning graph |
| `rags/memory-augmented-rag/` | Memory-Augmented RAG | LangGraph agent with `MemorySaver` and a retrieval tool |
| `rags/self-rag/` | Simplified Self-RAG | generation, binary self-critique, and at most one refinement |
| `eval-dataset/` | Shared dataset | 90 items (`Q001`–`Q090`) with question, answer, and source |

The original implementations and notebooks remain available for inspection. Installation, model-provider configuration, dataset selection, and execution policy are managed from the repository root.

## Sequential, resumable execution

Every pipeline processes the dataset once, one question at a time. After every attempt, the runner atomically updates:

- `rags/<pipeline>/results/checkpoint.json`: status, attempts, errors, and result for each ID when running locally;
- `rags/<pipeline>/results/results.csv`: cumulative successful rows without duplicate IDs across batches;
- `rags/<pipeline>/results/errors.json`: current failures with ID, question, attempt, type, message, output, and traceback.

On the next run, successful items are skipped. Failed, interrupted (`running`), and pending items are retried. The checkpoint also stores the dataset SHA-256 and refuses to combine results if the dataset changes.

For example, if only `Q037` fails in Self-RAG, running the same command again processes `Q037` without paying for the 89 successful questions again.

`--questions X` limits how many unresolved questions each selected RAG attempts in the current run. Repeating the command advances through the dataset in batches while preserving earlier successes in the same CSV. Previous failures are retried first; pending items then continue in dataset order. Omitting the flag processes the entire remaining dataset.

`X` applies **to each selected RAG**. Selecting three RAGs with `--questions 10` therefore allows up to 30 attempts in total: no more than 10 per pipeline. Selection is deterministic, and each run processes:

1. previously recorded failures, in dataset order;
2. pending questions, also in dataset order;
3. never any question already completed successfully.

`--questions` limits attempts, not successes. If two questions fail in a batch of 10, that run finishes after 10 attempts with eight new CSV rows and two error entries. The next run retries those failures before moving on to new questions.

### Concrete example: initial batch of 10

| First batch result | Saved state | Next run without `--questions` |
|---|---|---|
| All 10 questions succeed | 10 successes and 80 pending | skips the 10 completed questions and runs only the remaining 80 |
| 8 succeed and 2 fail | 8 successes, 2 failures, and 80 pending | skips the 8 successes, retries the 2 failures first, and then runs the 80 pending questions |
| All 10 fail | 10 failures and 80 pending | retries the 10 failures and then proceeds to the 80 pending questions |

A failure does not immediately stop the batch: it is recorded, and that RAG continues until reaching the run's attempt limit. When several RAGs are selected, the orchestrator also continues to the next pipeline; it returns exit code `1` at the end if any RAG had failures in that run.

If the second invocation also uses `--questions 10`, failures consume the first slots in that new batch. With two earlier failures, for example, the run retries those two and then attempts up to eight pending questions. Without the flag, there is no batch limit and the runner attempts every unresolved item.

### Result persistence

`checkpoint.json` is the source of truth. Instead of blindly appending lines, the runner atomically rebuilds `results.csv` from checkpoint successes after every attempt. This preserves dataset order, prevents duplicate IDs, and reduces the chance of a partial CSV after interruption.

`results.csv` contains successful questions only. `errors.json` contains only currently unresolved failures:

```json
{
  "version": 1,
  "project": "self-rag",
  "dataset": "/path/to/eval-dataset/qa_dataset_90.json",
  "dataset_sha256": "dataset-sha256",
  "updated_at": "2026-09-04T12:00:04+00:00",
  "count": 1,
  "errors": [
    {
      "id": "Q037",
      "question": "Question being evaluated",
      "attempts": 2,
      "started_at": "2026-09-04T12:00:00+00:00",
      "finished_at": "2026-09-04T12:00:04+00:00",
      "error_type": "RuntimeError",
      "message": "original exception message",
      "output": "RuntimeError: original exception message",
      "traceback": "Complete failure traceback"
    }
  ]
}
```

After a successful retry, the entry is removed from `errors.json` and added exactly once to the CSV. If the process stops while a question is marked `running`, that item becomes an `InterruptedRun` and is retried on the next invocation.

Artifacts live under `rags/<pipeline>/results/`. To start an evaluation from scratch, archive the pipeline's complete result directory so the checkpoint, CSV, and error JSON stay together.

## Requirements and uv installation

- Python 3.11, 3.12, or 3.13 for local execution;
- [`uv`](https://docs.astral.sh/uv/);
- Docker Desktop with Docker Compose only if you want to run Neo4j locally in a container;
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

The root `pyproject.toml` and `uv.lock` contain only orchestration dependencies. Each directory under `rags/` has its own `pyproject.toml`, `uv.lock`, and on-demand `.venv`. There are no `requirements.txt` files, so one pipeline can evolve its dependency set without changing another pipeline's environment.

## OpenRouter

OpenRouter is the default for chat and embeddings through its OpenAI-compatible `https://openrouter.ai/api/v1` endpoint. See the official [quickstart](https://openrouter.ai/docs/quickstart) and [Embeddings API](https://openrouter.ai/docs/api/reference/embeddings).

Minimal configuration:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=~openai/gpt-latest
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
rags/context-rag/docs/
rags/graph-rag/docs/
rags/hybrid-rag/docs/
rags/knowledge-enhanced-rag/data/apostilas/
rags/memory-augmented-rag/docs/
rags/self-rag/docs/
```

Each pipeline creates its own Chroma index on first run. Indexes, results, secrets, and virtual environments are ignored by Git.

## Optional local Neo4j

Benchmarks are controlled exclusively through the CLI. Docker is not required for the five RAGs that do not use Neo4j. To run `knowledge-enhanced-rag` with the local Neo4j service included in Compose, update `.env`:

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
uv run python main.py doctor
uv run python main.py run knowledge-enhanced-rag --questions 3
```

### Hosted Neo4j

To use Neo4j Aura or another hosted instance, do not run `docker compose`. Create the hosted database, copy the credentials provided by the service, and configure `.env`:

```env
NEO4J_URI=neo4j+s://your-id.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
```

Then run the benchmark directly:

```bash
uv run python main.py run knowledge-enhanced-rag --questions 3
```

`doctor` only checks that the variables exist; the actual connection is validated when the pipeline starts. The benchmark connects to the graph but does not populate it automatically. To build the curated graph before the benchmark, start the API in one terminal:

```bash
cd rags/knowledge-enhanced-rag
uv run python app.py
```

In another terminal, from the repository root, run:

```bash
curl -X POST http://127.0.0.1:8000/build-graph
```

Then stop the API and run the benchmark command. If the graph is not configured or is empty, `knowledge-enhanced-rag` can still use Chroma vector search alone.

## Usage

## Step-by-step execution

1. Place the PDFs directly in each RAG's `docs/` directory. For `knowledge-enhanced-rag`, use `data/apostilas/`.
2. Configure the key and model in `.env`.
3. Run the script. It validates the PDFs, runs `uv sync`, checks the configuration, and starts all six pipelines:

```bash
bash scripts/run_benchmark.sh
```

The script tests one question per pipeline by default. To test another batch size:

```bash
QUESTIONS=10 bash scripts/run_benchmark.sh
```

Ingestion happens automatically while each pipeline starts. The pipeline loads the PDFs, splits the text into chunks, generates embeddings, and writes the Chroma index before processing questions.

To test each RAG individually with up to three questions:

```bash
uv run python main.py run context-rag --questions 3
uv run python main.py run graph-rag --questions 3
uv run python main.py run hybrid-rag --questions 3
uv run python main.py run knowledge-enhanced-rag --questions 3
uv run python main.py run memory-augmented-rag --questions 3
uv run python main.py run self-rag --questions 3
```

Before testing, check the configuration and PDF counts:

```bash
uv sync
uv run python main.py doctor
```

`uv sync` installs dependencies but does not ingest documents. Ingestion happens when each RAG starts. Each RAG has its own Chroma index and stores results in `rags/<pipeline>/results/`.

```bash
# List the available pipelines
uv run python main.py list

# Check provider keys and local PDF counts
uv run python main.py doctor

# Run or resume one RAG
uv run python main.py run self-rag

# Run only the next 10 unresolved questions
uv run python main.py run self-rag --questions 10

# Run two or more RAGs in the requested order
uv run python main.py run context-rag hybrid-rag self-rag

# Run a batch of 15 questions in each selected RAG
uv run python main.py run context-rag hybrid-rag self-rag --questions 15

# Run or resume all six RAGs sequentially in isolated environments
uv run python main.py run all

# Run the next 5 questions in each of the six RAGs
uv run python main.py run all --questions 5

# Override the LLM provider for one run
uv run python main.py run hybrid-rag --provider openai

```

The previous `uv run python main.py run-all` command remains available as an alias.
`--limit` is an alias for `--questions`. The limit applies independently to every selected RAG; it is not divided among them.

A successful batch exits with code `0`, even when the selected limit leaves pending questions. The command exits with code `1` when one or more questions attempted in that run fail. This makes partial successful batches safe to use in CI scripts.

## Recommended execution flows

Process the dataset in batches of 10:

```bash
uv run python main.py run self-rag --questions 10
# Repeat until the summary reports 90 successes and no pending questions.
```

Resume after a failure, cancellation, or restart by running the same command:

```bash
uv run python main.py run self-rag --questions 10
```

No offset is required. The checkpoint skips successes, retries failures first, and continues from the next pending item. Do not edit only the CSV to change progress; `checkpoint.json` holds the authoritative state.

For valid comparisons, keep the generation model, embedding model, documents, and remaining `.env` settings equal across pipelines. `--provider` overrides the LLM provider for that invocation only; embeddings remain controlled by `EMBEDDING_PROVIDER`.

## Dataset and metrics

`eval-dataset/qa_dataset_90.json` has 90 objects with a stable `id`, `question`, `ground_truth`, and `source_book`. Each question is evaluated for `faithfulness`, `answer_relevancy`, `context_precision`, and `context_recall`. The CSV also records response time and input, output, and total token counts.

## Layout

```text
.
├── main.py                  # monorepo CLI
├── benchmark_runner.py      # per-question checkpoints and resume logic
├── rag_provider.py          # shared OpenRouter/OpenAI configuration
├── docker-compose.yml       # optional local Neo4j
├── pyproject.toml
├── uv.lock                 # orchestrator dependencies only
├── .env.example
├── eval-dataset/
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

## Common problems

- **Incompatible checkpoint:** the dataset content changed. Archive that pipeline's result directory and begin a new evaluation; do not combine different dataset versions.
- **Missing API key:** run `uv run python main.py doctor` and inspect the root `.env`, which is shared by all six RAGs.
- **Neo4j in Docker:** because benchmarks run on the host through the CLI, use `NEO4J_URI=bolt://127.0.0.1:7687` in `.env`.
- **Neo4j Aura:** use the instance-provided `neo4j+s://...` URI and matching credentials. Do not mix the password of the local persistent database with Aura credentials.
- **Changed embedding model:** choose another `CHROMA_PERSIST_DIR` or deliberately rebuild the index; different vector dimensions must not share a collection.

## Development checks

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check main.py benchmark_runner.py tests
```

The suite covers one/many/all RAG selection, limit validation, incremental runs without duplicate rows, failure-first resumption, and removal of recovered entries from `errors.json`.

## License

No software license has been declared yet. Until a `LICENSE` file is added, all rights to the code remain reserved. Corpus documents retain their original authors' rights and are not part of this monorepo distribution.
