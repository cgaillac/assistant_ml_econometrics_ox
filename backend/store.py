"""
Minimal SQLite-backed storage: student tokens, per-day usage counts, and a
full log of every exchange (for the course's Robustness & Verification
appendix, and for you to spot-check for abuse).

SQLite is deliberately chosen here: a class of <30 students generates a
trivial amount of data, and it means zero extra infrastructure (no separate
database service to pay for or manage). Swap for Postgres later if needed
without changing the API layer.
"""

import hashlib
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

DB_PATH = os.environ.get("AI_COMPANION_DB", "companion.db")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                token_hash TEXT PRIMARY KEY,
                student_label TEXT NOT NULL,
                daily_limit INTEGER NOT NULL DEFAULT 60,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL,
                student_label TEXT NOT NULL,
                persona TEXT NOT NULL,
                step TEXT,
                session_id TEXT,
                request_text TEXT,
                response_text TEXT,
                model TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                persona TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                turn_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """
        )


def add_token(student_label: str, token: str, daily_limit: int = 60):
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tokens (token_hash, student_label, "
            "daily_limit, created_at) VALUES (?, ?, ?, ?)",
            (_hash_token(token), student_label, daily_limit, time.time()),
        )


def lookup_token(token: str):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM tokens WHERE token_hash = ?",
            (_hash_token(token),),
        ).fetchone()
        return dict(row) if row else None


def usage_today(token_hash: str) -> int:
    start_of_day = time.time() - 24 * 3600
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM usage_log WHERE token_hash = ? "
            "AND created_at > ?",
            (token_hash, start_of_day),
        ).fetchone()
        return row["n"]


def export_usage_log(limit: int = 500, student_label: str | None = None):
    """Newest-first rows from usage_log, for the admin log-export endpoint."""
    with _conn() as conn:
        if student_label:
            rows = conn.execute(
                "SELECT student_label, persona, step, session_id, "
                "request_text, response_text, model, created_at "
                "FROM usage_log WHERE student_label = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (student_label, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT student_label, persona, step, session_id, "
                "request_text, response_text, model, created_at "
                "FROM usage_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def log_exchange(
    token_hash, student_label, persona, step, session_id,
    request_text, response_text, model,
):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO usage_log (token_hash, student_label, persona, "
            "step, session_id, request_text, response_text, model, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                token_hash, student_label, persona, step, session_id,
                request_text, response_text, model, time.time(),
            ),
        )


def get_or_create_session(session_id: str | None, persona: str, token_hash: str) -> tuple[str, int]:
    """Returns (session_id, current_turn_count_before_this_exchange)."""
    with _conn() as conn:
        if session_id:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                return session_id, row["turn_count"]
        new_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sessions (session_id, persona, token_hash, "
            "turn_count, created_at) VALUES (?, ?, ?, 0, ?)",
            (new_id, persona, token_hash, time.time()),
        )
        return new_id, 0


def increment_session_turn(session_id: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE sessions SET turn_count = turn_count + 1 "
            "WHERE session_id = ?",
            (session_id,),
        )


def hash_token(token: str) -> str:
    return _hash_token(token)
