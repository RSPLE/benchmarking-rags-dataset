from __future__ import annotations

import argparse
import json
import signal
import threading
import time

from app.benchmark.storage import exclusive_lock, now
from app.dashboard.config import Settings
from app.dashboard.database import backup_database, connect
from app.dashboard.ingest import synchronize


def checkpoint_database(settings):
    destination = settings.backups / "live.sqlite3"
    digest = backup_database(settings.database, destination)
    with connect(settings.database) as db, db:
        db.execute(
            "INSERT OR REPLACE INTO backups VALUES (?,?,?,?)",
            ("live", str(destination), digest, now()),
        )
    return destination


def backup_finished_runs(settings):
    count = 0
    for path in sorted(settings.results.glob("*/*/deliveries/*/metadata.json")):
        if not path.resolve().is_relative_to(settings.results):
            continue
        metadata = json.loads(path.read_text())
        identifier = metadata["run_id"]
        if len(identifier) != 32 or any(char not in "0123456789abcdef" for char in identifier):
            continue
        with connect(settings.database) as db:
            if db.execute("SELECT 1 FROM backups WHERE id=?", (identifier,)).fetchone():
                continue
        destination = settings.backups / f"completed-{identifier}.sqlite3"
        digest = backup_database(settings.database, destination)
        with connect(settings.database) as db, db:
            db.execute(
                "INSERT INTO backups VALUES (?,?,?,?)",
                (identifier, str(destination), digest, now()),
            )
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_environment()
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    last_backup = 0
    dirty = False
    with exclusive_lock(settings.database.with_suffix(".monitor.lock")):
        while not stopped.is_set():
            try:
                changed = synchronize(settings)
                dirty = dirty or bool(changed)
                finished = backup_finished_runs(settings)
                if (
                    args.once
                    or last_backup == 0
                    or (
                        (dirty or finished)
                        and time.monotonic() - last_backup >= settings.backup_seconds
                    )
                ):
                    checkpoint_database(settings)
                    last_backup, dirty = time.monotonic(), False
                print(
                    json.dumps({"at": now(), "changed": changed, "completed_backups": finished}),
                    flush=True,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                print(json.dumps({"at": now(), "error": type(exc).__name__}), flush=True)
                if args.once:
                    raise SystemExit(1) from None
            if args.once:
                break
            stopped.wait(settings.poll_seconds)
        if dirty:
            checkpoint_database(settings)


if __name__ == "__main__":
    main()
