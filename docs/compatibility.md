# Compatibility with the original experiments

[Português](compatibility.pt-BR.md) · [Configuration](configuration.md) · [Acceptance](acceptance.md)

## Audited sources

The October 7, 2026 comparison uses the Python scripts in the repositories below.
Notebooks remain historical material. Original prompt fingerprints and the CSV
header are pinned in `tests/fixtures/upstream_contracts.json` and regression-tested.

| RAG | Original revision | Preserved retrieval |
| --- | --- | --- |
| Context | [a8fdf885](https://github.com/RSPLE/context-rag/tree/a8fdf8859f19946cc3d777176a45e0b744ea9e72) | 800/100 chunks; vector top-5 |
| Hybrid | [b4855bd0](https://github.com/RSPLE/hybrid-rag/tree/b4855bd001a7844ebd4014bacc11f0359e5d2011) | 800/100 chunks; BM25 0.4 + vector 0.6; default `RETRIEVER_K=3` |
| Self | [3ad11ff8](https://github.com/RSPLE/self-rag/tree/3ad11ff823d169141c75ebfa018c3290ef395cd0) | 800/100 chunks; top-5; critique and at most one refinement |
| Memory | [8b43b663](https://github.com/RSPLE/memory-augmented-rag/tree/8b43b663db76ecb9c90f1a214061b3670c81f953) | 800/100 chunks; top-5 tool; fresh thread per question |
| Graph | [cc80d331](https://github.com/RSPLE/graph-rag/tree/cc80d331c122dc091e6fcba7a361b0959bac5dcc) | 1000/200 chunks; vector top-3; first 20 chunks extracted; depth 2 and up to 40 relevant nodes |
| Knowledge | [62898387](https://github.com/RSPLE/knowledge-enhanced-rag/tree/62898387c0c72fbb6e1a7de75c049987ad8a58e8) | 800/100 chunks; top-5; curated Neo4j; shared last five exchanges |

The `800/100` and `1000/200` pairs mean chunk size/overlap in the original splitter.
Original top-k and intrinsic graph parameters remain. No additional byte limit,
document truncation or global deduplication is introduced by the runner. The hybrid
retriever retains its original fusion behavior.

## Primary CSV and auxiliary files

All six pipelines produce `resultados/<rag>/<experiment_id>/results.csv`, with a
semicolon delimiter, UTF-8 BOM and no dataframe index, in this exact order:

```text
question;faithfulness;answer_relevancy;context_precision;context_recall;answer_response_time_seconds;answer_input_tokens;answer_output_tokens;answer_total_tokens
```

`<rag>-run-<repetition>_1.csv` contains identical bytes for consumers that find
original script filenames. Do not count the two files as separate repetitions.
Each row is a case with all four valid metrics, ordered by the dataset. Failures,
pending cases and missing scores never become zeros. The CSV does not establish
complete coverage: inspect `summary.json` and metric denominators before comparing.

| File | Purpose |
| --- | --- |
| `results.csv` and original-name copy | Primary tables, plots and Telegram export |
| `results_detailed.csv` | IDs, references, answers, context counts and primary fields; private |
| `checkpoint.json` | Answers, documentary contexts, additional evidence and individual metrics; private |
| `public_results.json` | IDs and public CSV fields, without answers or evidence |
| `summary.json` | Coverage, means, failures, stage and known/unknown usage |
| `usage.jsonl` and `budget.jsonl` | Preparation, generation, embedding and judge usage, separate from answer tokens |

`answer_*` measures the answer stage, retaining the original column purpose; it is
not total experiment cost. Reusing indices/retrieval avoids work and changes observed
latency. `evaluate` preserves original time/tokens when supplied by the frozen
export; missing values remain missing.

The local plotting script accepts an experiment folder from any of the six RAGs:

```bash
uv run --locked --project app/rags/context-rag python app/rags/context-rag/plot_graph.py --results-dir resultados/context-rag/EXP --output mean_metrics.png
```

It reads original-name files only, rejects invalid/missing metrics and never turns
NaN into zero. Aggregating multiple repetition files computes a mean of means;
verify matching coverage, protocol and models first.

## Evaluation evidence and generation evidence

Audited original answer, critique/refinement and extraction prompts are preserved.
For the primary CSV, `contexts` contains separate documentary chunks rather than
one concatenated item. This matters to
[RAGAS context precision](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/),
which considers relevance and the positions of retrieved items.

Graph and Memory retain the original-question vector search for evaluation. When
the tool already performed that exact search, its documents are reused. If the
agent rewrote the query, the original-question documentary retrieval still runs,
as in the audited scripts. Tool contents are recorded separately as
`generation_contexts` and `generation_evidence_metadata`.

Knowledge passes PDF chunks to RAGAS. Facts, prerequisites and next concepts remain
in the generator prompt and additional saved fields. Primary faithfulness therefore
measures documentary support; it does not automatically cover all graph or history
evidence. Mixing those sources into the primary CSV would change the original
protocol. An alternative evaluation needs a separately identified experiment.

## Knowledge session and declared differences

The selected protocol for 90 questions uses a shared session, as in the original
chatbot. Generation receives the last five question/answer pairs and the complete
new prompt. The checkpoint saves `conversation_history` and `generation_order`
after each generation. Resume restores the latest saved conversation; retrying
only the judge neither adds an exchange nor regenerates an answer. Generation
failures can change the effective order, which remains recorded. Reproduction
requires the same sequence and disclosure of failures.

The upstream script retains the chatbot across its five internal rounds too.
Here `--repetition` creates independent experiments, each starting with fresh
memory; history does not cross experiment IDs. The shared session covers the
90-question experiment and its resumptions. This does not reproduce the entire
upstream sequence of five rounds in a single session.

Reliability changes are explicit: Knowledge's duplicate retrieval is removed;
indices and Graph extraction can be reused; answers and individual scores survive
retries. Self retains the original critique text without an added context field,
but normalizes `SIM`, `NAO` and `NÃO`; other critique outputs fail instead of silently
being accepted. Knowledge requires Neo4j in `required` mode rather than silently
continuing without its graph.

CSV/prompt compatibility does not guarantee identical paper scores. The
90-question dataset, corpus, GLM/embedding models, versions, memory, repetitions
and coverage belong in the published method. The manifest identifies those
conditions; do not mix legacy and new experiments under one ID.

## Shared flags

`main.py run`, `run-all`, `resume` and bot commands `/executar` and `/retomar`
recognize the same options:

| Flag | Effect |
| --- | --- |
| `--questions N`, `--limit N` | Up to N attempts per RAG; Telegram accepts 1 through 90 |
| `--provider openrouter\|openai` | Generation/judge provider; embeddings retain their own settings |
| `--mode full\|evaluate` | Generation + evaluation or frozen evaluation only |
| `--frozen PATH` | Saved answers for a single RAG in `evaluate` mode |
| `--selection unresolved\|pending\|failed` | Eligible cases for this batch |
| `--max-calls N` | Maximum calls per batch |
| `--max-seconds N` | Batch deadline |
| `--question-timeout N` | Question deadline |
| `--repetition N` | Independent scientific repetition identity |

Omitted options use `.env`. With no count, the bot admits up to 90 questions.
`/executar all` queues six RAGs sequentially and cancels the remainder after the
first error; repeated commands carrying the same update ID never duplicate jobs.
Legacy `/executar RAG N [USD]` remains accepted. No USD cap is mandatory. Bot
`--frozen` paths are restricted to the documented import directories.

```bash
uv run --locked python -m app run all --questions 1 --max-seconds 3600
uv run --locked python -m app resume knowledge-enhanced-rag EXP --questions 3
```

```text
/executar all --questions 1 --max-seconds 3600
/retomar knowledge-enhanced-rag EXP --questions 3
/resultado knowledge-enhanced-rag EXP
```

`EXP` is the complete experiment hash. Resume rejects changes to model, corpus,
code or repetition; count, selection and deadline do not change methodology.
Commands come from an authorized human's private bot conversation; the channel
receives results.
