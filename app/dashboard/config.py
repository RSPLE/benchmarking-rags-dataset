from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.paths import ROOT


@dataclass(frozen=True)
class Settings:
    database: Path
    results: Path
    legacy: Path
    backups: Path
    poll_seconds: int = 15
    backup_seconds: int = 60
    session_seconds: int = 28800
    username: str = ""
    password: str = field(default="", repr=False)
    control_socket: Path = Path("/run/benchmark/control.sock")
    control_user_id: int = 0

    @classmethod
    def from_environment(cls):
        from dotenv import dotenv_values

        configured = (
            dotenv_values(ROOT / ".env", interpolate=False)
            if os.getenv("PYTHON_DOTENV_DISABLED") != "1"
            else {}
        )
        values = {**configured, **os.environ}
        control_user = (
            values.get("DASHBOARD_CONTROL_USER_ID")
            or str(values.get("TELEGRAM_ALLOWED_USER_IDS", "") or "").split(",")[0].strip()
            or "0"
        )

        def path(name, default):
            return (ROOT / values.get(name, default)).resolve()

        return cls(
            path("DASHBOARD_DATABASE", "app/dashboard/state/dashboard.sqlite3"),
            path("DASHBOARD_RESULTS_DIR", values.get("BENCHMARK_OUTPUT_DIR", "resultados")),
            path("DASHBOARD_LEGACY_DIR", "app/rags"),
            path("DASHBOARD_BACKUP_DIR", "backups/dashboard"),
            max(5, int(values.get("DASHBOARD_POLL_SECONDS", "15"))),
            max(15, int(values.get("DASHBOARD_BACKUP_SECONDS", "60"))),
            max(300, int(values.get("DASHBOARD_SESSION_SECONDS", "28800"))),
            values.get("DASHBOARD_USERNAME", "") or "",
            values.get("DASHBOARD_PASSWORD", "") or "",
            path("BENCHMARK_CONTROL_SOCKET", "/run/benchmark/control.sock"),
            int(control_user),
        )
