# Web dashboard and Docker

[Português](README.md) · [Deployment](../deployment/README.en.md)

## Start the project

Set these fields in the root `.env` before startup:

```dotenv
DASHBOARD_USERNAME=your_username
DASHBOARD_PASSWORD='your-password-with-at-least-12-characters'
```

Choose your own credentials. Usernames accept 3–64 letters, digits, dots, hyphens
and underscores. Passwords require 12–1024 characters. Single quotes preserve
characters such as `$` when Compose reads the value. Keep `.env` private, with
mode `0600`, excluded from Git and Docker images.

Then, from the project root:

```bash
docker compose up -d
```

Open <http://127.0.0.1:8501> and sign in using your `.env` credentials.
**There are no default credentials, post-start registration or extra account
creation commands.** Compose requires both fields, and the dashboard validates
them before starting its server. The password remains in `.env` as configured;
SQLite stores only a scrypt hash with an individual salt.

This command starts Neo4j, the dashboard, importer/backup, control and Telegram.
The four application services build from local Dockerfiles using the build cache.
No profiles or private project images are required. Registry login is unnecessary.
The first build needs internet access for public base images and dependencies
pinned in the lockfiles.

Starting services makes the queue and bot available according to the existing
Telegram settings in `.env`. Request new benchmarks through CLI or bot commands.
The dashboard reads results and does not initiate model calls.

## Inspect and restart

```bash
docker compose ps
docker compose logs --tail 80 dashboard monitor control telegram neo4j
```

Wait for `healthy` status on dashboard, importer, control and Neo4j. Telegram only
operates with its token, channel and activation settings configured. Missing or
short credentials prevent access; inspect logs without printing the entire `.env`.

To change the password, edit `DASHBOARD_PASSWORD` and run again:

```bash
docker compose up -d
```

The account updates before server startup and previous sessions are invalidated.
Restarting with the same password preserves the account and existing lockouts.
Five failed attempts lock login for five minutes. Changing the username creates
another account and preserves previous accounts; it does not revoke the old name.
Dashboard credentials are independent of Neo4j, Telegram and OpenRouter.

## Persistence and backup

`dashboard-state` stores SQLite accounts and imported results. `dashboard-backups`
stores database copies. Other volumes retain the queue, notifications, Neo4j and
RAG indices. `BENCHMARK_OUTPUT_DIR=resultados` is mounted as an actual host
directory, read-only for importing/Telegram and writable by the executor.
Historical files in `rags/*/results*` are read without modification.

Containers use UID/GID 1000. The results directory must be readable by the importer
and writable by the executor. Avoid `777` permissions and recursive changes to
historical results. Do not use `docker compose down -v` to restart: it deletes
persistent volumes.

Request a consistent online SQLite backup with:

```bash
docker compose exec monitor python -m dashboard.manage backup
```

Do not manually copy the live SQLite file instead of using its backup API.
Local backups do not replace copies stored outside the machine.

## Options in the same `.env`

| Variable | Default | Purpose |
|---|---|---|
| `DASHBOARD_USERNAME` | Required | Your username |
| `DASHBOARD_PASSWORD` | Required | Your password, at least 12 characters |
| `DASHBOARD_BIND_ADDRESS` | `127.0.0.1` | Docker published address |
| `DASHBOARD_PORT` | `8501` | Dashboard port |
| `DASHBOARD_POLL_SECONDS` | `15` | Import/refresh interval |
| `DASHBOARD_BACKUP_SECONDS` | `60` | Minimum periodic backup interval when data changes |
| `DASHBOARD_SESSION_SECONDS` | `28800` | Maximum login session lifetime |

On a VPS, keep the port bound to localhost and connect from your computer using
`ssh -L 8501:127.0.0.1:8501 user@server`, then open the same local URL. Configure
HTTPS and a reverse proxy before publishing the login page on the internet.

## Neo4j

`Invalid admin username, it must be neo4j` is fixed: local initialization uses the
`neo4j` administrator. Its password comes from `NEO4J_PASSWORD` and only applies
when the volume has no existing authentication. Do not delete existing data to
change credentials.

The executor respects `.env` values for `NEO4J_URI` and `NEO4J_USERNAME` when using
a hosted database. The dashboard does not depend on Neo4j. To select the local
Compose database explicitly:

```dotenv
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_CONTAINER_URI=bolt://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-local-password
```

For hosted databases, leave `NEO4J_CONTAINER_URI` empty. An empty local Neo4j does
not automatically contain Knowledge's research graph; pipeline checks still
verify that graph before benchmarks.

## Main chart

`rags/context-rag/plot_graph.py` separates figure creation
(`build_overall_figure`) from saving (`plot_overall_mean`). The dashboard reuses
the same figure for PNG/EPS. This extraction does not change means, bars, colors,
labels, CSVs or existing CLI behavior.

## Without Docker

With the same credentials in `.env`, run from the root:

```bash
uv sync --project dashboard --frozen
uv run --project dashboard --frozen python -m dashboard.monitor
```

In another terminal, also from the root:

```bash
uv run --project dashboard --frozen python -m dashboard.server
```

Local mode uses `dashboard/state/dashboard.sqlite3` and `backups/dashboard`.
These files are separate from Docker volumes, although credentials are loaded
from the same `.env`.
