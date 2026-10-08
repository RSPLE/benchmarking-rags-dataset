from __future__ import annotations

import argparse
import os
import sys

from app.dashboard.auth import bootstrap_account
from app.dashboard.config import ROOT, Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    settings = Settings.from_environment()
    try:
        bootstrap_account(settings.database, settings.username, settings.password)
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    os.chdir(ROOT / "app/dashboard")
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            f"--server.address={args.address}",
            f"--server.port={args.port}",
        ],
    )


if __name__ == "__main__":
    main()
