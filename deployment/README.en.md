# Deployment and recovery

[Português](README.md) · [Single configuration](../docs/configuration.en.md) · [Operations](../docs/reliability.en.md)

## Preparation without paid execution

1. Record VPS commit, local changes, datasets, results, corpus, indices and service
   state. Preserve `.env` without printing its contents.
2. Make consistent backups. Chroma must not be used by a worker. Use a Neo4j
   backup/dump compatible with the installed version and restore into a separate
   database; copying a live volume does not prove a consistent backup.
3. Transfer verified code and PDFs without replacing historical results, `.env`,
   databases or indices. Sync the root and all six environments with
   `uv sync --locked --python 3.12`, adding `--project rags/NAME` for each child.
4. Run `main.py preflight` and the documented suites; resolve blockers.
5. Create system users `benchmark` and `benchmark-notifier`, both in group
   `benchmark`. The latter must not read credentials/checkpoints. Grant the worker
   write access to results, indices and `/var/lib/benchmark`. Deployed code must be
   readable by services but administered separately from their accounts.
6. Complete only `/logibot/benchmarking-rags-dataset/.env`, with administrative
   ownership and mode `0600`, following the configuration guide. Do not create
   `worker.env`, `control.env`, `telegram.env` or `profiles.json`. Default `full`
   mode covers all six RAGs; Knowledge uses `required`, with PDFs plus Neo4j.
7. Result directories use group benchmark and traversal mode `0750`. Public files
   use `0640`; checkpoints and financial journals use `0600`. Unit StateDirectory
   settings prepare `/var/lib/benchmark` and `/var/lib/benchmark-notifier`.
8. Install `control.service` as `benchmark-control.service` and `telegram.service`
   as `benchmark-telegram.service` during connection. `systemctl daemon-reload`
   loads units without queuing work. Restart services after editing `.env`.

Examples use the supplied VPS paths; adjust for other deployments. The UV cache
uses `/var/lib/benchmark/uv-cache`; environments must be prepared before startup.
`service_entrypoint.py` selects role-specific variables before starting the final
process. Control receives human IDs without the bot token; the notifier receives
its token/destination without model or Neo4j credentials. Systemd reads
`EnvironmentFile` before blocking service access to `.env`. Locked python-dotenv
versions support `PYTHON_DOTENV_DISABLED`, which the launcher sets to prevent
subsequent file reads in child processes.

`benchmark.service` is a one-shot alternative. It requires `BENCHMARK_PROJECT` and
`BENCHMARK_QUESTION_LIMIT` in the same `.env`. Do not supervise the same work twice.
The usual Telegram flow uses only control and notification services and does not
require these two one-shot variables.

## Telegram connection

1. The bot and private channel already exist. Set `TELEGRAM_BOT_TOKEN` to the full
   BotFather token and `TELEGRAM_RESULTS_CHAT_ID` to the channel ID, including its
   negative sign. The numeric bot ID cannot replace either value.
2. Give the bot channel administrator rights to post messages. Start a private
   conversation with the bot and populate `TELEGRAM_ALLOWED_USER_IDS` with human IDs.
3. Enable `TELEGRAM_ENABLED` and `TELEGRAM_CONTROL_ENABLED`, keeping
   `BENCHMARK_REMOTE_ENABLED=false` during dummy delivery and `/status` checks.
4. Test reconnection and rejection of another user. Only one process may consume
   getUpdates for the token; remove any existing webhook before using polling.
5. Verify models, seven PDFs, Neo4j graph, accounting and technical limits.
   There is no mandatory dollar cap or financial reservation without an active cap.
6. Only then enable `BENCHMARK_REMOTE_ENABLED=true`, restart control and send
   `/executar context-rag 1` in the private conversation with the bot. This can incur
   model costs during preparation. Follow progress in the channel.
7. Inspect `/status` and the financial journal before increasing batch size.
   `/retomar` requires compatible settings and the full experiment ID.

No deployment, live Telegram connection or paid call occurred in this revision.
Changing the local `.env` does not automatically update the VPS copy.

## Recovery

Control restarts mark queued/running jobs interrupted. Paid work never restarts
without an explicit request. Inspect state and usage, reconcile where possible
and submit a new resume request. A charged call without a saved response may need
investigation and must not be considered free.

Test shutdown during a simulated job, restoration into an isolated directory and
notifier restart. Measure VPS CPU/RAM/time before increasing batches. For rollback,
stop admitting jobs and use a separate checkout of the earlier code; do not mix old
checkpoints with new experiments.
