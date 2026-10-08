# VPS deployment with Docker Compose

[Português](README.pt-BR.md) · [Configuration](../docs/configuration.md) · [Dashboard](../dashboard/README.md)

Run these commands from the repository root. The supported deployment requires
Docker Engine and the Docker Compose plugin on the VPS; Python/uv are not needed
on the host. Check `docker --version` and `docker compose version` before starting.

## 1. Clone or transfer the project

```bash
git clone https://github.com/RSPLE/benchmarking-rags-dataset.git
cd benchmarking-rags-dataset
```

The checkout must contain the revision with `app/` and the new Dockerfiles. Local
changes must be transferred or published before cloning them on another machine.
Do not copy `.venv`, caches or containers. For an existing VPS installation, preserve
`.env`, `resultados/`, indices and volumes, and retain the original Compose project name.

## 2. Create the single configuration file

For a new installation:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

If `.env` already exists, edit it without overwriting its credentials. Configure:

| Variable | Value |
| --- | --- |
| `DASHBOARD_PUBLIC_HOST` | Public VPS IP, such as `179.236.251.180`, or domain; no scheme or port |
| `DASHBOARD_USERNAME` | Your username: 3–64 letters, digits, `.`, `_` or `-` |
| `DASHBOARD_PASSWORD` | Your password, at least 12 characters; use single quotes if it contains `$` |
| `OPENROUTER_API_KEY` | Real OpenRouter key |
| `OPENROUTER_MODEL`, `OPENROUTER_JUDGE_MODEL`, `OPENROUTER_EMBEDDING_MODEL` | Protocol models; an empty judge model inherits the main model |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | Knowledge graph database connection |
| `NEO4J_CONTAINER_URI` | `bolt://neo4j:7687` for the Compose database; empty to use the hosted URI |
| `TELEGRAM_BOT_TOKEN` | Complete BotFather token, not just the numeric bot ID |
| `TELEGRAM_RESULTS_CHAT_ID` | Negative numeric private channel ID, usually `-100...` |
| `TELEGRAM_ALLOWED_USER_IDS` | Your numeric personal user ID; comma-separated for several operators |
| `BENCHMARK_REMOTE_ENABLED` | `true` to admit dashboard and Telegram jobs |
| `TELEGRAM_ENABLED`, `TELEGRAM_CONTROL_ENABLED` | `true` for publications and commands |
| `TELEGRAM_SEND_FINAL_FILES` | `true` to publish public artifacts after each RAG round |

There is no mandatory monetary cap. Technical call, token and time limits remain
configurable. The dashboard has no default login credentials.

For local Neo4j, use `NEO4J_USERNAME=neo4j`, choose a password, set
`NEO4J_URI=bolt://127.0.0.1:7687` and `NEO4J_CONTAINER_URI=bolt://neo4j:7687`.
For Aura/another server, use its credentials and leave `NEO4J_CONTAINER_URI` empty.
Changing the password does not reset authentication in an existing Neo4j volume.

## 3. Check PDFs, result directory and ports

Place the same seven approved PDFs in `app/rags/context-rag/docs/` if missing.
Compose mounts that directory at `/corpus` for all six RAGs, including Knowledge;
six host copies are unnecessary. PDFs are not baked into images. The shared
question dataset is already in `data/evaluation/`.

Services write as UID/GID 1000. For the default `BENCHMARK_OUTPUT_DIR=resultados`,
create the directory on a new installation:

```bash
sudo install -d -m 0750 -o 1000 -g 1000 resultados
```

Omit `sudo` when logged in as root. For another output path, create that directory
with the same ownership. Preserve existing results and ensure access before running.
Do not use `777` permissions.

Allow **TCP 80 and 443** in both VPS and provider firewalls while preserving SSH
access. These ports must be available and reach this VPS. Do not directly publish
Streamlit, authentication, queue, SQLite or Neo4j. Telegram uses outbound connections.

## 4. Start all services

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
```

Images build locally from public bases. The first startup requires internet access
and may take time. No profiles or private registry login are required. Startup
does not automatically schedule a benchmark. The root Caddyfile is copied into
the proxy image: the next `docker compose up -d` applies changes to that file.

Open `https://<DASHBOARD_PUBLIC_HOST>` with the credentials set in `.env`.
On the computer running Docker, use `http://127.0.0.1:8501`.
Your laptop's `127.0.0.1` does not refer to the VPS. Public certificate issuance
requires the configured address and ports to reach the VPS; running locally with
a remote IP does not validate HTTPS on that VPS.

```bash
docker compose logs --tail 80 proxy auth dashboard monitor control telegram
docker compose exec control python -m app doctor
docker compose exec control python -m app preflight
docker compose exec control python -m app telegram-check
```

`doctor` checks configuration/corpus; `preflight` reads PDFs and dependencies without
embedding or model calls. Reading the seven PDFs may take time. `telegram-check`
uses the API to check bot/channel permissions without sending messages or consuming
updates. The bot must be a channel administrator with posting permission.

## 5. Prepare the Knowledge graph when needed

`BENCHMARK_KG_MODE=required` requires a populated curated graph. Do not rebuild an
existing hosted/restored research graph. A new empty Neo4j database does not contain
it automatically. Knowledge continues to use the same seven PDFs.

Only for a database without `Conceito` nodes, the command below initializes the
graph defined in the original code and refuses an existing concept graph. Run it
before submitting jobs; keep `BENCHMARK_ALLOW_KG_REBUILD=false` in `.env`:

```bash
docker compose exec -T -w /app/app/rags/knowledge-enhanced-rag control .venv/bin/python - <<'PY'
import os
import rag_settings
from src.knowledge_graph import KnowledgeGraph

graph = KnowledgeGraph()
try:
    with graph.driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        count = session.run("MATCH (n:Conceito) RETURN count(n) AS total").single()["total"]
    if count:
        raise SystemExit("The graph already contains concepts; preserve the existing database.")
    os.environ["BENCHMARK_ALLOW_KG_REBUILD"] = "true"
    graph.build_graph()
finally:
    graph.fechar()
PY
```

This writes to the configured Neo4j database without calling models or extracting
a new graph from PDFs. Preserve/restore existing research databases according to
their protocol; do not substitute another graph to work around a failure.

## 6. Start and follow work

Open **Executar e retomar** in the dashboard, select RAG, attempt count and flags,
then submit. **Pendências e falhas** prepares resumptions. Closing the browser does
not stop the worker. Send Telegram commands in a private conversation with the bot:

```text
/start
/status
/executar context-rag --questions 1
/retomar context-rag FULL_ID --questions 3 --selection failed
```

The channel receives results; execution commands belong in the private conversation.
Equivalent terminal commands inside the worker container:

```bash
docker compose exec control python -m app run context-rag --questions 1
docker compose exec control python -m app resume context-rag FULL_ID --questions 3 --selection failed
```

These start real work and may consume credits from preparation onward. CLI,
dashboard and Telegram share flags and checkpoints. An active run holds a global
lock; do not submit another batch while it is running. See the
[options and protocol](../docs/compatibility.md).

## 7. Preserve data and update

`resultados/` stays on the host. SQLite, queue, Telegram outbox, indices and
certificates use persistent volumes. The monitor makes consistent SQLite backups
after changes; Telegram sends public artifacts after each round according to
configuration. The channel does not replace a complete backup: preserve private
checkpoints, indices and databases outside the VPS as well.

```bash
docker compose exec monitor python -m app.dashboard.manage backup
docker compose ps
```

Update when no job is active. Preserve data, update code and run
`docker compose up -d`. Do not use `docker compose down -v`: `-v` deletes volumes.
Resuming requires compatible settings; see the [migration guide](../docs/layout.md).
For refused WebSocket connections, inspect `proxy`, `auth` and `dashboard` logs;
the proxy must use the current image, verifying the HTTP session before upgrading.

For deployment without Compose, see the [systemd guide](systemd.md). Do not run
both supervisors against the same jobs or Telegram polling session.

## Public IP access and TLS diagnostics

On the VPS, open `https://<DASHBOARD_PUBLIC_HOST>` without `:8501`. Port 8501 is
published only on `127.0.0.1` for local access. A timeout when another computer
requests `http://IP:8501` is consistent with that restriction.

The Caddyfile sets `default_sni {$DASHBOARD_PUBLIC_HOST}` to select the configured
certificate for clients without SNI, needed for this IP deployment behind Docker.
See the [Caddy documentation](https://caddyserver.com/docs/caddyfile/options#default-sni).
The address still comes exclusively from `.env`, without a hardcoded IP.

To diagnose without disabling certificate verification:

```bash
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' http://VPS_IP
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' https://VPS_IP/auth/login
docker compose logs --tail 80 proxy
```

Replace `VPS_IP` with the actual address. HTTP should redirect to HTTPS, and the
login page should return 200 with a trusted certificate. `health: starting` just
after startup is transient; the local healthcheck does not validate public HTTPS.
If 80/443 already respond, inspect TLS logs before changing firewall rules.
After a Caddyfile update, `docker compose up -d` applies the new image.
To update only this service, use `docker compose up -d --no-deps proxy`.
