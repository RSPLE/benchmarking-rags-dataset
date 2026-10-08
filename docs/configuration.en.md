# Single configuration file and private channel

[Português](configuracao.md) · [Operations](reliability.en.md) · [Deployment](../deployment/README.en.md)

Maintain only the repository root `.env`. `.env.example` is a credential-free
template, not a second configuration to maintain. The local file is
`/home/ramon/Git/GitHub/LogiBots/benchmarking-rags-dataset/.env`. Service units use
`/logibot/benchmarking-rags-dataset/.env` on the VPS. Preserve VPS-specific addresses
and credentials during deployment; do not blindly replace its configuration with
the local copy. Separate Telegram/control environment files are no longer needed.

## Where Telegram values belong

| Variable in `.env` | Required value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Complete BotFather token, including the numeric prefix and secret separated by `:`. The numeric bot ID alone cannot authenticate. |
| `TELEGRAM_RESULTS_CHAT_ID` | Numeric **private channel ID**, including its negative sign; it usually begins with `-100`. Do not use the bot ID, personal user ID or invitation link. |
| `TELEGRAM_ALLOWED_USER_IDS` | Your numeric **human user ID** authorized to send commands to the bot. Separate multiple users with commas. Do not use channel IDs, bot IDs or `@usernames`. |

The `CHAT_ID` name also applies to channels: the
[Telegram Bot API](https://core.telegram.org/bots/api#sendmessage) uses `chat_id`
for the message destination. Add the bot as a channel administrator with permission
to post messages. Channel membership does not authorize benchmark control;
`TELEGRAM_ALLOWED_USER_IDS` controls that access.

The channel receives progress, scores and public CSV/JSON files. To start, pause
or inspect tests, use your personal account in a **private conversation with the
bot**. Channel posts are not accepted as commands, allowing the requester to be
identified and authorized.

These three fields remain empty because their values were not supplied. Existing
model and Neo4j credentials were preserved. Do not put secrets in documentation,
commits or shell commands.

## Staged activation

Initial settings are:

```dotenv
TELEGRAM_ENABLED=false
TELEGRAM_CONTROL_ENABLED=false
BENCHMARK_REMOTE_ENABLED=false
```

After entering the values and preparing VPS services:

1. Set `TELEGRAM_ENABLED=true` and `TELEGRAM_CONTROL_ENABLED=true`. Keep
   `BENCHMARK_REMOTE_ENABLED=false` while checking publication and `/status` without RAG execution.
2. Restart both services after editing `.env`; processes read environment settings
   at startup.
3. After verifying connectivity, set `BENCHMARK_REMOTE_ENABLED=true` and restart
   control to allow starting/resuming jobs. This setting alone does not queue work.
4. `/executar context-rag 1` starts up to one attempt and may incur model costs
   during preparation. `/retomar context-rag EXP 1` requires the full experiment
   hash. Use `/status` to inspect saved IDs.

The delivered `.env` has no dollar cap. Usage remains recorded, and technical call,
token, time and retry limits remain active. USD is an optional command argument
for enabling a specific allowance; that use also requires a per-call reservation,
as explained in the operations guide.

## Other fields

- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` and `OPENROUTER_EMBEDDING_MODEL` preserve
  existing provider/model choices. An empty `OPENROUTER_JUDGE_MODEL` uses the general model.
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` and `NEO4J_DATABASE` identify
  Knowledge's database. Check VPS-specific values during deployment.
- `BENCHMARK_MODE=full` enables all six architectures without a profiles file.
- `BENCHMARK_OUTPUT_DIR` and `BENCHMARK_BUDGET_DIR` point to `resultados`, resolved
  from the repository root, for shared outputs and accounting.
- `BENCHMARK_CONTROL_SOCKET`, `BENCHMARK_CONTROL_DATABASE` and
  `BENCHMARK_TELEGRAM_DATABASE` already match VPS service paths.
- `TELEGRAM_PROGRESS_INTERVAL_SECONDS=15` sets polling frequency;
  `TELEGRAM_SEND_FINAL_FILES=true` includes public exports when completed.

Optional advanced settings, including role-specific keys, embedding dimensions,
snapshots and dollar caps, remain documented in the operations guide and are not
needed in the usual configuration. Do not set a global shared
`CHROMA_PERSIST_DIR` for all six RAGs; each architecture keeps its own index.

## What Knowledge's `data` means

| Path | Origin and purpose |
| --- | --- |
| `rags/knowledge-enhanced-rag/data/apostilas/` | Original ingestion path. Contains copies of the same seven PDFs, checked against Context RAG using SHA-256. |
| `data/knowledge-graph.json` | Additional snapshot alternative copied from the 14 concepts and relationships curated in the original code. Not extracted from the PDFs; inactive. |
| `eval-dataset/qa_dataset_90.json` | Shared evaluation dataset of 90 questions and references. Does not replace the PDFs and is never used as an automatic candidate answer. |

The delivered setting is `BENCHMARK_KG_MODE=required`: PDFs plus Neo4j. Services
do not automatically select the JSON or build a different corpus. The curated
graph was part of the original architecture and provides knowledge beyond the
PDFs. Replacing it with a graph extracted from the books would change the method
and requires a separate decision. Historical results are not rewritten.

## How services read configuration

`service_entrypoint.py` starts control, batch or Telegram with only the variables
for that role. The notifier receives no model keys; workers receive no Telegram
token. With the systemd units, the manager reads `.env` before making it inaccessible
inside service processes. Keep the file mode `0600` and administrative ownership
on the VPS. Git ignores it.

For manual startup from the root, use `uv run --locked python service_entrypoint.py
control` or `uv run --locked python service_entrypoint.py telegram`, with configured
directories accessible to the current user. These commands start services; they
are not dry runs. `main.py` also loads the same `.env` for RAG CLI execution.
