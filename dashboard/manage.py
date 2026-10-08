from __future__ import annotations

import argparse
import getpass
import hashlib
import sqlite3
from pathlib import Path

from benchmark_archive import restore_archive
from dashboard.auth import set_password
from dashboard.config import Settings
from dashboard.database import backup_database, connect
from dashboard.ingest import synchronize
from dashboard.monitor import backup_finished_runs, checkpoint_database


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("create-user", "reset-password"):
        commands.add_parser(command).add_argument("username")
    commands.add_parser("sync")
    commands.add_parser("backup")
    restore = commands.add_parser("restore-db")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)
    archive = commands.add_parser("restore-results")
    archive.add_argument("source", type=Path)
    archive.add_argument("destination", type=Path)
    verify = commands.add_parser("verify-backup")
    verify.add_argument("source", type=Path)
    args = parser.parse_args()
    settings = Settings.from_environment()
    if args.command in {"create-user", "reset-password"}:
        if settings.password and args.username.strip().lower() == settings.username.lower():
            parser.error(
                "This account is configured in .env. Update DASHBOARD_PASSWORD "
                "there, then run docker compose up -d."
            )
        password = getpass.getpass("Senha / Password: ")
        if getpass.getpass("Confirme / Confirm: ") != password:
            parser.error("Passwords do not match")
        set_password(
            settings.database, args.username, password, replace=args.command == "reset-password"
        )
        checkpoint_database(settings)
        print("User saved. No plaintext password was stored.")
    elif args.command == "sync":
        print("Changed experiments:", synchronize(settings))
        backup_finished_runs(settings)
        checkpoint_database(settings)
    elif args.command == "backup":
        print(checkpoint_database(settings))
    elif args.command == "restore-db":
        if args.destination.exists() or not args.source.is_file():
            parser.error("Use an existing backup and a new destination")
        with sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True) as source:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                parser.error("Backup is corrupt")
        backup_database(args.source, args.destination)
        print(
            "Restored into",
            args.destination,
            "— select this path explicitly before restarting services.",
        )
    elif args.command == "restore-results":
        print("Restored files:", restore_archive(args.source, args.destination))
    else:
        with connect(args.source) as source:
            print("SQLite:", source.execute("PRAGMA integrity_check").fetchone()[0])
        print("SHA-256:", hashlib.sha256(args.source.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
