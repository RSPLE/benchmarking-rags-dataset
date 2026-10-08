# Deployment and recovery

[Português](README.pt-BR.md) · [Single configuration](../docs/configuration.md) · [Operations](../docs/reliability.md)

For Streamlit, follow the [dashboard guide](../dashboard/README.md), including
the single `docker compose up -d` command, credentials set in `.env` before startup,
HTTPS through the root Caddyfile and access at `https://<DASHBOARD_PUBLIC_HOST>`. Allow TCP
80/443 in the VPS/provider firewall. Database, queue and Telegram do not need public
ports. The dashboard starts and resumes jobs through the same queue as the bot.
The systemd workflow below covers execution and Telegram.

## Preparation without paid execution

1. Record VPS commit, local changes, datasets, results, corpus, indices and service
   state. Preserve `.env` without printing its contents.
2. Make consistent backups. Chroma must not be used by a worker. Use a Neo4j
   backup/dump compatible with the installed version and restore into a separate
   database; copying a live volume does not prove a consistent backup.
3. Transfer verified code and PDFs without replacing historical results, `.env`,
   databases or indices. Sync the root and all six environments with
   `uv sync --locked --python 3.12`, adding `--project app/rags/NAME` for each child.
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
`app/services/entrypoint.py` selects role-specific variables before starting the final
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

The local `.env` already contains the verified channel, bot token and authorized
human ID, with publication/control/remote admission enabled. Preserve the correct
Neo4j endpoint and credentials for the VPS when transferring configuration.

```bash
uv run --locked python -m app telegram-check
sudo install -m 0644 app/deployment/control.service /etc/systemd/system/benchmark-control.service
sudo install -m 0644 app/deployment/telegram.service /etc/systemd/system/benchmark-telegram.service
sudo systemctl daemon-reload
sudo systemctl enable --now benchmark-control.service benchmark-telegram.service
sudo systemctl status benchmark-control.service benchmark-telegram.service
```

Run these commands after preparing users, permissions, environments and directories
as described above. Starting services admits commands but does not schedule a
benchmark. Keep only one polling process. In the private bot conversation use
`/start`, then `/status`. The channel receives publications; `/start` posted in the
channel does not execute work. When ready, use `/executar context-rag --questions 1`
or `/executar all --questions 1`. These commands can incur model costs from preparation.
All shared CLI flags are documented in [compatibility](../docs/compatibility.md).

Live read-only Telegram checks passed in this review. Message delivery, service
execution, Neo4j and a real model pilot still require deployment validation. No VPS
service was activated or paid model call made. Editing local `.env` does not update
the VPS automatically. If a token has been exposed, replace it via BotFather and
update `TELEGRAM_BOT_TOKEN` before starting services.

## Recovery

Control restarts mark queued/running jobs interrupted. Paid work never restarts
without an explicit request. Inspect state and usage, reconcile where possible
and submit a new resume request. A charged call without a saved response may need
investigation and must not be considered free.

Test shutdown during a simulated job, restoration into an isolated directory and
notifier restart. Measure VPS CPU/RAM/time before increasing batches. For rollback,
stop admitting jobs and use a separate checkout of the earlier code; do not mix old
checkpoints with new experiments.
