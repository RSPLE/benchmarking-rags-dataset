from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database: Path
    results: Path
    legacy: Path
    backups: Path
    poll_seconds: int = 15
    backup_seconds: int = 60
    session_seconds: int = 28800

    @classmethod
    def from_environment(cls):
        from dotenv import dotenv_values

        configured = (
            dotenv_values(ROOT / ".env") if os.getenv("PYTHON_DOTENV_DISABLED") != "1" else {}
        )
        values = {**configured, **os.environ}

        def path(name, default):
            return (ROOT / values.get(name, default)).resolve()

        return cls(
            path("DASHBOARD_DATABASE", "dashboard/state/dashboard.sqlite3"),
            path("DASHBOARD_RESULTS_DIR", values.get("BENCHMARK_OUTPUT_DIR", "resultados")),
            path("DASHBOARD_LEGACY_DIR", "rags"),
            path("DASHBOARD_BACKUP_DIR", "backups/dashboard"),
            max(5, int(values.get("DASHBOARD_POLL_SECONDS", "15"))),
            max(15, int(values.get("DASHBOARD_BACKUP_SECONDS", "60"))),
            max(300, int(values.get("DASHBOARD_SESSION_SECONDS", "28800"))),
        )
