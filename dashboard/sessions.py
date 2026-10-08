from __future__ import annotations

import hashlib
import re
import secrets
import time

from dashboard.database import connect

COOKIE_NAME = "rag_session"


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(database, identity, ttl):
    token = secrets.token_urlsafe(32)
    current = time.time()
    with connect(database) as db, db:
        db.execute("DELETE FROM web_sessions WHERE expires_at<=?", (current,))
        user = db.execute(
            "SELECT version FROM users WHERE username=?", (identity["username"],)
        ).fetchone()
        if not user or user[0] != identity["version"]:
            raise ValueError("Account changed during login")
        db.execute(
            "INSERT INTO web_sessions VALUES (?,?,?,?,?)",
            (token_hash(token), identity["username"], user[0], current, current + ttl),
        )
    return token


def session_identity(database, token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    with connect(database) as db:
        row = db.execute(
            "SELECT s.username,s.version,s.issued_at,s.expires_at FROM web_sessions s "
            "JOIN users u ON u.username=s.username AND u.version=s.version "
            "WHERE s.token_hash=? AND s.expires_at>?",
            (token_hash(token), time.time()),
        ).fetchone()
    return dict(row) if row else None


def revoke_session(database, token):
    if not isinstance(token, str):
        return
    with connect(database) as db, db:
        db.execute("DELETE FROM web_sessions WHERE token_hash=?", (token_hash(token),))


def csrf_token(token):
    return hashlib.sha256(("rag-csrf:" + token).encode()).hexdigest()
