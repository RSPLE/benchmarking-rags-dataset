# Benchmark reliability and operation

[Português](confiabilidade.md)

## Scope and current status

Six architectures evaluate the canonical 90-question dataset. Remote control uses
Python, a local Unix socket and the Telegram Bot API exclusively. There is no
integration with external administrative agents.

The agreed sequence is implementation and offline verification, Telegram setup,
then starting VPS tests through the bot. The operator removed the project dollar
cap and already has a bot and private channel. Their token, channel ID and authorized
human user IDs still need to be entered in the single root `.env`. No paid call has
been made in this stage. See the [configuration guide](configuration.en.md).

## Preservation and experiment identity

Historical results and datasets remain at their original paths. Do not edit them
to accommodate the new runner. `main.py migrate SOURCE DEST` is a dry run; `--apply`
creates an independent archive while preserving the source. Legacy checkpoints
without context text cannot reproduce the original evidence-based evaluation.

New outputs use `resultados/<rag>/<experiment_id>/`. Manifests identify code,
libraries, dataset, corpus, models, parameters, evidence policy, index and mode.
Method changes create new experiments. `/retomar` requires the full ID and rejects
an incompatible configuration before making external calls.

Checkpoints, exports and manifests use atomic replacement and fsync. Answers and
evidence are saved before judging; each metric is persisted independently.
Compatible successes are reused. Historical events remain after recovery. Old
indices are never deleted automatically.

## Corpus, evidence and indices

Knowledge uses the same seven PDFs as the other architectures.
`python scripts/prepare_corpus.py` creates this copy with hash verification and
refuses to overwrite a different corpus. A new deployment
must transfer them; a repository clone does not guarantee their presence.
`main.py preflight` checks all six environments and extracts PDF text locally,
without calling models.

Retrieval deduplicates identical document text and selects whole documents up to
`BENCHMARK_CONTEXT_MAX_BYTES` (32000), preserving selected order and metadata.
Evidence actually passed to tools/agents is saved. `BENCHMARK_INPUT_MAX_BYTES`
(128000) rejects larger serialized requests. These are UTF-8 byte limits, not exact
counts from each model's tokenizer. Token reservations use serialized input bytes
plus the output allowance, explicitly recorded as a conservative estimate.

Chroma manifests and deterministic IDs allow missing chunks to resume. Manifests
record observed vector dimensions; dimension, corpus or ID mismatches reject reuse.
When configured, `EMBEDDING_DIMENSIONS` is sent to the provider and checked against
stored vectors. Provider support must be verified in the pilot. Legacy indices
without manifests are not automatically adopted; initial ingestion may cost money.

Graph caches extraction of the same first twenty chunks. This architecture is not
Microsoft GraphRAG. Self receives evidence during critique, normalizes SIM/NAO/NÃO
and permits one refinement. Independent questions do not share conversation memory.

## Knowledge and Neo4j

The default is `BENCHMARK_KG_MODE=required`: the same seven PDFs under
`rags/knowledge-enhanced-rag/data/apostilas/`, plus the curated Neo4j graph. This
PDF path already existed in the pipeline; it is not another evaluation dataset.
The new root `data/knowledge-graph.json` is an inactive alternative. It was not
extracted from the PDFs, does not replace the books and is not selected by services.
The curated learning graph was part of the original architecture. Extracting a new
graph from the PDFs would be a separate methodological change and was not done.

`BENCHMARK_KG_MODE` supports:

- `snapshot`: reads `BENCHMARK_KG_SNAPSHOT` without Neo4j. The supplied
  `data/knowledge-graph.json` represents the graph curated in the original code,
  with 14 concepts, relationships and a verified hash. It is not a VPS backup.
  The loaded copy is immutable throughout a case.
- `required`: requires a reachable, populated Neo4j database and checks its identity
  before each question. Concurrent writes still require operational isolation.
- `disabled`: explicitly records absence of graph evidence as a different experiment.

Neo4j graph replacement is disabled by default. After consistent backup and database
isolation, the operator may set `BENCHMARK_ALLOW_KG_REBUILD=true`. The historical
rebuild replaces `Conceito` nodes; understand this effect before using it.

## Budgets, credentials and retries

Preparation, generation, embeddings and each metric record calls in `usage.jsonl`.
The shared financial journal is `budget.jsonl` under `BENCHMARK_BUDGET_DIR`. Use the
same directory across services/output roots. Its lock admits one worker at a time.
Unrelated consumers of the provider key are outside this project's control.

| Variable | Default | Meaning |
| --- | ---: | --- |
| `BENCHMARK_MAX_CALLS` | 200 | Calls per batch, including retries |
| `BENCHMARK_MAX_QUESTION_CALLS` | 60 | Calls per question |
| `BENCHMARK_MAX_PREPARATION_CALLS` | 100 | Preparation calls |
| `BENCHMARK_MAX_TOKENS` | 1000000 | Known tokens and reservations per batch |
| `BENCHMARK_MAX_QUESTION_TOKENS` | 100000 | Per-question tokens, including preparation |
| `BENCHMARK_MAX_COST_USD` | 0 | Batch cost |
| `BENCHMARK_MAX_QUESTION_COST_USD` | 0 | Question cost |
| `BENCHMARK_MAX_PREPARATION_COST_USD` | 0 | Preparation cost |
| `BENCHMARK_MAX_DAILY_COST_USD` | 0 | Cost attributed to the UTC call-start date |
| `BENCHMARK_MAX_PERIOD_COST_USD` | 0 | All-time cost in the financial directory |
| `BENCHMARK_RESERVE_COST_USD` | 0 | Estimated reservation per in-flight call |
| `BENCHMARK_MAX_SECONDS` | 3600 | Batch deadline |
| `BENCHMARK_QUESTION_TIMEOUT_SECONDS` | 900 | Question deadline |
| `BENCHMARK_MAX_STAGE_ATTEMPTS` | 3 | Maximum stage failures across restarts |
| `BENCHMARK_RETRY_COOLDOWN_SECONDS` | 60 in pipelines | Delay before retrying a case |

Zero disables a monetary cap; it does not mean free usage. HTTP calls with an active
monetary cap require a positive reservation. This reservation is neither a fetched
price nor an exact billing ceiling. Missing cost blocks further monetarily limited
calls. Provider key/account limits remain complementary protection. Do not erase
the financial journal to restart an exhausted pilot.

The delivered configuration has no dollar cap; monetary variables are optional
and default to zero when absent. Remote commands do not require a budget argument.
Technical call, token, time and retry limits remain active, as does accounting.
When an optional cap is configured, omitting USD does not bypass it; a positive
reservation is then required. Actual cost may exceed that estimate.

Optional role credentials: `OPENROUTER_GENERATION_API_KEY`,
`OPENROUTER_JUDGE_API_KEY`, `OPENROUTER_EMBEDDING_API_KEY`, and
`OPENROUTER_JUDGE_EMBEDDING_API_KEY`; equivalent `OPENAI_*` variables are supported.
Missing role credentials fall back to the general key; judge embeddings prefer the
judge key. `BENCHMARK_CREDIT_SCOPE=judge` blocks generation/preparation. The service
launcher consumes the single `.env` and replaces itself with a process whose
environment contains only role-specific settings. The notifier rejects model
credentials. Systemd units make `.env` inaccessible inside services after the manager
reads it; no derived credential files need maintenance.

SDK retries are disabled. HTTP 429/503 honor bounded Retry-After waits. RAGAs defaults
to one attempt with bounded format recovery. Credential/configuration/credit errors
pause the batch. Network timeouts are not replayed automatically because they may
already have been charged.

Pause requests also block the next call during preparation, generation or judging,
including after SIGTERM. An already sent request may finish.
Deadlines limit admission, waits and HTTP timeouts; asynchronous requests have an
overall deadline. The supervised worker terminates its process group when the batch
or question deadline expires, allowing 15 seconds after SIGTERM before SIGKILL. Interrupted calls
may lack saved responses; that is ambiguous usage, not free execution.

## Reconciliation and frozen answers

```bash
uv run --locked python main.py reconcile /var/lib/benchmark/budget
uv run --locked python main.py reconcile /var/lib/benchmark/budget --apply
uv run --locked python main.py export-frozen resultados/RAG/EXPERIMENT /path/answers.json
uv run --locked python main.py run context-rag --mode evaluate --frozen /path/answers.json --questions 1
uv run --locked python main.py report resultados
```

Without `--apply`, reconciliation is a dry run. `BENCHMARK_RECONCILE_ON_START=true`
also attempts reconciliation before a batch. Applying it fetches metadata for
identifiable OpenRouter generations and appends an event; it never generates content
or repeats a question. Calls without recoverable IDs remain unknown. Zero cost is
never invented. The lookup uses the recorded role's credentials.

Export requires an intact `full`-mode v2 checkpoint and saved evidence. `evaluate` validates
questions, references and provenance and prohibits generation/preparation. Reference
answers are never substituted automatically for candidate answers.

## Remote control and Telegram

`benchmark_control.py` owns a private SQLite queue and listens only on its configured
Unix socket. The socket group is the local trust boundary. The notifier runs as a
separate user without access to checkpoints or worker credentials. Numeric IDs are
validated in both gateway and control service. Commands are accepted only in a
private conversation with the bot. The private channel receives publications;
channel/group posts cannot authorize execution. Free-form instructions and shell
commands are rejected. `TELEGRAM_RESULTS_CHAT_ID` holds the channel ID, not the bot ID.

| Command | Effect |
| --- | --- |
| `/status` | Lists recent jobs and experiments |
| `/status RAG EXP` | Shows saved state |
| `/executar RAG N [USD]` | Queues up to N attempts; USD is optional |
| `/retomar RAG EXP N [USD]` | Resumes the compatible experiment; USD is optional |
| `/pausar RAG EXP` | Requests cooperative pause |
| `/falhas RAG EXP` | Shows saved failures |
| `/pergunta RAG EXP Q001` | Shows case state and metrics, without private text |
| `/resultado RAG EXP` | Sends public score JSON |

EXP is the complete 64-character hash. Buttons offer status, pause and results.
Services use the root `.env`, with `BENCHMARK_MODE=full` for all six RAGs. Maintaining
`profiles.json` is unnecessary. The control CLI still supports `--profiles` for
explicit advanced configurations. Using `evaluate` through `.env` requires
`BENCHMARK_PROJECT` and `BENCHMARK_FROZEN_FILE`. Telegram commands cannot choose
arbitrary paths or credentials. Example without a dollar cap: `/executar context-rag 1`.

The command ID derives from the Telegram update and is persisted with job creation.
Replaying a command cannot create another job. After a service restart, previously
queued/running jobs become interrupted and require a new request. Only one process
should consume getUpdates for the bot token.

Public start, failure and completion events survive notifier downtime. The durable
outbox stores messages/CSV/JSON and respects retry_after. A crash between
sending and acknowledging may duplicate a message, never a paid benchmark. Summaries
include metric coverage, known/unknown cost, stage, duration and alerts. Prompts,
answers, contexts, keys and raw tracebacks are not published.

## Deployment and verification

See [deployment/README.en.md](../deployment/README.en.md) and service examples.
Do not enable remote execution before preparing environments, checking backups,
configuring `.env`, technical limits and the bot destination. Examples do not install services.

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/verify_integrations.py
uv run --locked python main.py preflight
uv run --locked ruff check --exclude '*.ipynb' .
uv run --locked ruff format --check --exclude '*.ipynb' --exclude '*.md' .
git diff --check
```

The [acceptance matrix](acceptance.en.md) separates automated checks from live
validation. Tests use fake models/HTTP and real RAGAs, LangChain and Chroma in all
six environments. They do not certify model quality or provider availability.
Historical notebooks are not supported benchmark entrypoints.

Interface references: [Telegram Bot API](https://core.telegram.org/bots/api) and
[OpenRouter generation metadata](https://openrouter.ai/docs/api/api-reference/generations/get-request-&-usage-metadata-for-a-generation).
