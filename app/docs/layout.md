# Application layout and existing experiments

[Português](layout.pt-BR.md) · [Repository](../../README.md) · [Deployment](../deployment/README.md)

All implementation and support directories are under `app/`:

| Path | Responsibility |
| --- | --- |
| `app/__main__.py`, `app/cli.py` | Shared `python -m app` command |
| `app/benchmark/` | Runner, evaluation, checkpoints, exports, queue, accounting and experiment identity |
| `app/dashboard/` | Streamlit, authentication, SQLite, charts and monitoring |
| `app/rags/` | Six isolated research pipelines and their original environments |
| `app/providers/` | Model/embedding clients and RAGAS compatibility |
| `app/telegram/` | Notifications, commands and Telegram configuration checks |
| `app/services/` | Service launch and role-specific environment variables |
| `app/tools/` | Corpus preparation and offline integration checks |
| `app/scripts/` | Shell convenience commands |
| `app/deployment/` | Dockerfiles, VPS instructions and optional systemd units |
| `app/docs/` | Configuration, methodology, operations and acceptance evidence |
| `app/tests/` | Common regression tests and upstream contract fixtures |
| `app/tests/dashboard/` | HTTP authentication and proxy integration tests |

The root retains `.env`, `.env.example`, Compose, Caddyfile, Python dependency files
and the main READMEs. `data/` contains scientific inputs; `resultados/` and `backups/`
contain runtime data. English READMEs are named `README.md`; Portuguese versions
are named `README.pt-BR.md`. No Python compatibility wrappers remain at the root.

## Commands from the repository root

```bash
uv run --locked python -m app list
uv run --locked python -m app doctor
uv run --locked python -m app preflight
uv run --locked python -m app.tools.verify_integrations
uv run --locked python -m unittest discover -s app/tests -v
uv run --project app/dashboard --locked python -m unittest discover -s app/tests/dashboard -v
uv run --locked ruff check app --exclude '*.ipynb'
```

The former root `main.py` is now `python -m app`. Service commands use
`python -m app.services.entrypoint`. In Compose, prefix scientific CLI commands
with `docker compose exec control`; dependencies are already installed in images.
Local virtual environments are not portable artifacts: on another machine,
recreate them from their unchanged lockfiles. Do not copy `.venv` to the VPS.

## Preserving results

Dataset JSON, notebooks, historical CSV/checkpoint files, plots and lockfiles retain
their contents. Docker volume names and internal results/database locations remain
unchanged. Keep the same Compose project name and volumes during upgrades; changing
the checkout directory may change Compose's default project name.

For local execution, the former `dashboard/state/` belongs at `app/dashboard/state/`,
with the database still named `dashboard.sqlite3`. Existing imported historical
rows are associated with their new `app/rags/` source only when their artifact hash
matches; the database IDs and stored samples are retained instead of duplicated.

## Resuming a v2 checkpoint across this move

Experiments include code hashes and index locations. Moving the code therefore
changes the identity of newly created experiments. An explicit `resume` can retain
an old identity only for the exact before/after revisions registered in
`app/benchmark/layout_v1.json`:

1. Validate the saved manifest's own hash and complete original code inventory.
2. Validate every current scientific/service module against the approved revision.
3. Accept only the registered `rags/` to `app/rags/` default index-path move.
4. Require identical corpus, dataset, runtime, models, metrics and other configuration.
5. Keep the original manifest/checkpoint and record the executed code's manifest
   in `layout_transitions/<current_experiment_id>.json`.

Completed answers and metrics remain reusable. An incompatible or unknown revision
is rejected before model preparation. Custom index paths must stay unchanged.
This is not a general code-compatibility bypass: future edits require a new
experiment or a separately audited migration. Do not regenerate the registry to
silence a mismatch. A different VPS runtime or absolute index location can also
prevent a resume; preserving files alone does not establish scientific compatibility.

Old v1 outputs remain available for inspection/import. They are not automatically
converted into v2 experiments; follow the [recovery guide](reliability.md).
