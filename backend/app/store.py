from __future__ import annotations

import secrets
import sqlite3
import uuid
from datetime import datetime, timezone


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

def upsert_user(conn: sqlite3.Connection, *, email: str, name: str) -> str:
    """Create the user, or update the stored name for a returning email.

    Assessment 4.1: email exists -> load their projects; otherwise create.
    """
    conn.execute(
        """
        INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,?)
        ON CONFLICT(email) DO UPDATE SET name = excluded.name
        """,
        (new_id(), email, name, now_iso()),
    )
    return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]


def get_user(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_session(conn: sqlite3.Connection, user_id: str) -> str:
    """An opaque 256-bit token. The row lives in SQLite so a restart does not
    sign anyone out (design 8.1)."""
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
        (token, user_id, now_iso()),
    )
    return token


def user_for_session(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT users.* FROM users JOIN sessions ON sessions.user_id = users.id "
        "WHERE sessions.token = ?",
        (token,),
    ).fetchone()


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
