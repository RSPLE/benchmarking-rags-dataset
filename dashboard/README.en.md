# Web dashboard, execution and VPS publishing

[Português](README.md) · [Deployment](../deployment/README.en.md)

## Start

Set your credentials in the single root `.env` before startup:

```dotenv
DASHBOARD_USERNAME=your_username
DASHBOARD_PASSWORD='your-password-with-at-least-12-characters'
DASHBOARD_PUBLIC_HOST=VPS_PUBLIC_IP
```

Usernames accept 3–64 letters, digits, dots, hyphens and underscores. Passwords require
12–1024 characters. Single quotes preserve `$` when Compose reads the value. Keep
`.env` mode `0600`, excluded from Git and images. There is no default account/password.

```bash
docker compose up -d
```

- On the VPS: `https://<DASHBOARD_PUBLIC_HOST>`.
- On the same computer: <http://127.0.0.1:8501>.

Replace `VPS_PUBLIC_IP` with the public IP or domain, without a scheme or port.
Compose reads `DASHBOARD_PUBLIC_HOST` from `.env` and passes it to the `proxy`
service. The root [Caddyfile](../Caddyfile) uses that variable without a fixed or
default address. Missing or empty values stop Compose with a configuration error.
The proxy protects the UI, downloads and WebSocket connection. Public HTTP
redirects to HTTPS. Port 8501 is restricted to localhost and uses the same
authentication; Streamlit is not published directly.

Allow **TCP 80 and 443** in the VPS/provider firewall and leave these ports available
for Caddy. This IP must reach the VPS over the internet to issue the certificate.
Caddy requests and renews certificates automatically, storing them in `caddy-data`.
The ACME `shortlived` profile supports
[Let's Encrypt public IP certificates](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability).
Actual issuance can only be confirmed after deployment with reachable ports.
Running on your local computer does not validate HTTPS on the public IP.

There are seven services: proxy, authentication, dashboard, importer/backup,
executor, Telegram and Neo4j. The five application services build locally. Caddy,
Neo4j and base images are public. No profiles, private project images or
`docker login` are required. The initial build needs internet access. Do not expose
additional ports for SQLite, authentication, queue, Neo4j or Telegram. The bot uses
outbound polling and does not need a public webhook/port.

## Persistent login and navigation

Reloading, changing pages or restarting containers preserves login until session
expiry, by default eight hours after authentication. The browser stores an
`HttpOnly`, `SameSite=Lax` cookie, also marked `Secure` over HTTPS. SQLite stores
only the session token hash and a scrypt password hash. Passwords do not appear in
URLs or browser local storage. Clearing cookies or opening another private window
requires login again. **Sair da conta** immediately revokes the session.

Changing `DASHBOARD_PASSWORD` and repeating `docker compose up -d` updates the
account and invalidates prior sessions. Five incorrect passwords lock the account
for five minutes. Changing only the username creates another account and keeps
the previous one. Existing accounts are operators who can submit jobs; this version
does not provide a viewer role.

The sidebar includes overview, execution/resume, pending/failed cases, original
charts, tokens/time, questions, calls/models and files/backups. The selected page
stays in the URL across reloads. On mobile, open the sidebar using its top button.
Dark styling is included; **⋮ → Theme** switches between light and dark,
with the preference saved in the browser. Paper charts keep their original style
even when the dashboard uses dark mode.

## Execute, resume and pause

1. Open **Executar e retomar** and choose one of the six RAGs or all sequentially.
2. Choose to start or resume an existing experiment with a v2 checkpoint.
3. Select pending only, failures only or both, and the attempt count.
4. Under **Parâmetros de execução**, adjust provider, mode, frozen answers,
   repetition, calls and timeouts. Zero in optional limits inherits configuration;
   it does not remove an existing limit.
5. Click **Enviar lote para execução**. Queue and usage can be followed in the
   dashboard and Telegram. Closing the browser does not stop the executor.

**Pendências e falhas** shows coverage and failures by stage. Its resume button
prepares the execution form without starting model calls. Completed cases are
reused by the same executor/checkpoint as CLI and Telegram. Incompatible settings
are rejected. Historical CSVs without v2 checkpoints remain available for analysis
but cannot be used as resume points.

The UI uses the internal control socket and existing parser, without shell
execution. `BENCHMARK_REMOTE_ENABLED=true` admits jobs from both dashboard and bot.
The control identity defaults to the first `TELEGRAM_ALLOWED_USER_IDS` entry;
optionally, `DASHBOARD_CONTROL_USER_ID` selects another ID already authorized in
that list. The real `.env` already contains an authorized ID.

One shared queue prevents concurrent batches and assigns an identifier to each
submission to avoid duplicates on resend. To repeat the same batch after completion,
choose **Preparar outro pedido**. Active execution offers **Pausar preservando o
checkpoint**. Pause is cooperative and depends on the active call. Restarting the
controller marks active jobs interrupted; resuming requires an explicit request.

Opening or refreshing a page does not call models. Submitting a job may consume
APIs from preparation/embeddings through evaluation. Operational controls do not
change RAG prompts, memory, retrieval, PDFs or metrics.

## Persistence, backup and diagnostics

`dashboard-state` retains SQLite, accounts, sessions and imported results;
`dashboard-backups` stores consistent copies. `caddy-data` and `caddy-config` keep
certificates/configuration. Other volumes retain the queue, notifications, Neo4j
and indices. `BENCHMARK_OUTPUT_DIR=resultados` remains an actual host directory.
The monitor reads new and historical results without changing them.

Application containers use UID/GID 1000. Results must be readable by the monitor
and writable by the executor. Avoid `777` permissions. Do not restart using
`docker compose down -v`: `-v` deletes persistent volumes.

```bash
docker compose ps
docker compose logs --tail 80 proxy auth dashboard monitor control telegram
docker compose exec monitor python -m dashboard.manage backup
```

Backup uses SQLite's online backup API; do not replace it by copying the live file.
The existing routine sends public results and hashes to Telegram at the end of
each round according to configuration. It excludes passwords/private checkpoints.
Keep copies outside the VPS too.

## Options in the same `.env`

| Variable | Default | Purpose |
|---|---|---|
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | Required | Credentials set before startup |
| `DASHBOARD_PUBLIC_HOST` | Required, no default | Public IP or domain pointing to the VPS |
| `DASHBOARD_HTTP_PORT` / `DASHBOARD_HTTPS_PORT` | `80` / `443` | Public ports; retain defaults for ACME without external forwarding |
| `DASHBOARD_PORT` | `8501` | Authenticated localhost access |
| `DASHBOARD_POLL_SECONDS` | `15` | Import and refresh interval |
| `DASHBOARD_BACKUP_SECONDS` | `60` | Minimum periodic backup interval when data changes |
| `DASHBOARD_SESSION_SECONDS` | `28800` | Total login lifetime in seconds |
| `DASHBOARD_CONTROL_USER_ID` | First authorized Telegram ID | Operator identity for queue requests |

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

## Development

Compose is the supported publishing path and includes proxy/authentication.
Running Streamlit alone does not create an authenticated session. The scientific
executor remains available through the CLI documented at the repository root.

Validate the dashboard without paid model calls:

```bash
uv sync --project dashboard --frozen
uv run --project dashboard --frozen python -m unittest discover -s dashboard/tests -v
uv run --locked python -m unittest discover -s tests -v
```
