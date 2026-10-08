from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from app.dashboard.database import connect


def password_digest(password, salt):
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
    ).hex()


def validate_username(username):
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,64}", username):
        raise ValueError("Username must contain 3–64 letters, digits, underscores, dots or hyphens")
    return username


def password_record(password):
    if not 12 <= len(password) <= 1024:
        raise ValueError("Use a password with 12–1024 characters")
    salt = secrets.token_hex(16)
    digest = password_digest(password, salt)
    return salt, digest


def bootstrap_account(database, username, password):
    if not username or not password:
        raise ValueError("Set DASHBOARD_USERNAME and DASHBOARD_PASSWORD in .env before startup")
    username = validate_username(username)
    if not 12 <= len(password) <= 1024:
        raise ValueError("DASHBOARD_PASSWORD must contain 12–1024 characters")
    with connect(database) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row and hmac.compare_digest(
            row["password_hash"], password_digest(password, row["salt"])
        ):
            return
        salt, digest = password_record(password)
        db.execute(
            "INSERT INTO users (username,password_hash,salt,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, "
            "salt=excluded.salt, failed_attempts=0, locked_until=0, version=users.version+1, "
            "updated_at=excluded.updated_at",
            (username, digest, salt, time.time()),
        )


def set_password(database, username, password, *, replace=False):
    username = validate_username(username)
    salt, digest = password_record(password)
    with connect(database) as db, db:
        exists = db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if exists and not replace:
            raise ValueError("User exists; use reset-password explicitly")
        db.execute(
            "INSERT INTO users (username,password_hash,salt,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, "
            "salt=excluded.salt, failed_attempts=0, locked_until=0, version=users.version+1, updated_at=excluded.updated_at",
            (username, digest, salt, time.time()),
        )


def authenticate(database, username, password):
    username = username.strip().lower()
    if len(username) > 64 or len(password) > 1024:
        return None
    with connect(database) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        salt = row["salt"] if row else "00" * 16
        candidate = password_digest(password, salt)
        valid = bool(row and hmac.compare_digest(candidate, row["password_hash"]))
        current = time.time()
        if row and row["locked_until"] > current:
            return None
        if not valid:
            if row:
                attempts = row["failed_attempts"] + 1
                db.execute(
                    "UPDATE users SET failed_attempts=?,locked_until=? WHERE username=?",
                    (attempts, current + 300 if attempts >= 5 else 0, username),
                )
            return None
        db.execute(
            "UPDATE users SET failed_attempts=0,locked_until=0 WHERE username=?", (username,)
        )
        return {"username": username, "version": row["version"], "issued_at": current}


def valid_session(database, session, ttl):
    if not isinstance(session, dict) or time.time() - session.get("issued_at", 0) > ttl:
        return False
    with connect(database) as db:
        row = db.execute(
            "SELECT version FROM users WHERE username=?", (session.get("username"),)
        ).fetchone()
        return bool(row and row["version"] == session.get("version"))
