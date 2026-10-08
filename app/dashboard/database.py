from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, salt TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY, project TEXT NOT NULL, external_id TEXT NOT NULL,
    source TEXT NOT NULL UNIQUE, origin TEXT NOT NULL, model TEXT NOT NULL,
    judge_model TEXT NOT NULL, comparison_key TEXT NOT NULL,
    manifest TEXT NOT NULL, summary TEXT NOT NULL, revision TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    experiment_id TEXT NOT NULL REFERENCES experiments(id), question_id TEXT NOT NULL,
    position INTEGER NOT NULL, question TEXT NOT NULL, status TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY (experiment_id, question_id)
);
CREATE TABLE IF NOT EXISTS calls (
    experiment_id TEXT NOT NULL REFERENCES experiments(id), call_id TEXT NOT NULL,
    question_id TEXT, stage TEXT, model TEXT, kind TEXT, tokens REAL, cost REAL,
    seconds REAL, payload TEXT NOT NULL, PRIMARY KEY (experiment_id, call_id)
);
CREATE TABLE IF NOT EXISTS runs (
    experiment_id TEXT NOT NULL REFERENCES experiments(id), run_id TEXT NOT NULL,
    started_at TEXT, finished_at TEXT, seconds REAL,
    PRIMARY KEY (experiment_id, run_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
    experiment_id TEXT NOT NULL REFERENCES experiments(id), name TEXT NOT NULL,
    sha256 TEXT NOT NULL, content BLOB NOT NULL, PRIMARY KEY (experiment_id, name)
);
CREATE TABLE IF NOT EXISTS revisions (
    experiment_id TEXT NOT NULL REFERENCES experiments(id), revision TEXT NOT NULL,
    imported_at TEXT NOT NULL, PRIMARY KEY (experiment_id, revision)
);
CREATE TABLE IF NOT EXISTS sync_errors (
    source TEXT PRIMARY KEY, error TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username),
    version INTEGER NOT NULL, issued_at REAL NOT NULL, expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS web_sessions_expiry ON web_sessions(expires_at);
"""


@contextlib.contextmanager
def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=15)
    os.chmod(path, 0o600)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript(SCHEMA)
    try:
        yield db
    finally:
        db.close()


def backup_database(source, destination):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Backup destination must differ from the live database")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".sqlite-", dir=destination.parent)
    os.close(fd)
    try:
        with sqlite3.connect(source, timeout=15) as reader, sqlite3.connect(temporary) as writer:
            reader.backup(writer, pages=256, sleep=0.01)
            if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite backup integrity check failed")
        with open(temporary, "rb") as saved:
            os.fsync(saved.fileno())
        digest = hashlib.sha256(Path(temporary).read_bytes()).hexdigest()
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return digest
    finally:
        Path(temporary).unlink(missing_ok=True)
