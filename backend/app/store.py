from __future__ import annotations

import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

from app.models import ProjectListItem
from app.steps import (
    ProjectStatus,
    StepState,
    completed_steps,
    current_step,
    display_status,
    needs_attention,
)


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


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

def create_project(conn, *, project_id: str, user_id: str, title: str, book_path: str,
                   book_excerpt: str) -> str:
    """The id is supplied by the caller, because the book file is written to a
    project-scoped directory before the row exists (design 3.2)."""
    conn.execute(
        """
        INSERT INTO projects (id, user_id, title, created_at, book_path, book_excerpt,
                              status, step_state)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (project_id, user_id, title, now_iso(), book_path, book_excerpt,
         ProjectStatus.CREATED, StepState.IDLE),
    )
    return project_id


def get_project(conn, project_id: str, user_id: str) -> sqlite3.Row | None:
    """Ownership is part of the lookup. A miss is a 404 either way, so another
    user's project is never confirmed to exist (design 8.2)."""
    return conn.execute(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
    ).fetchone()


def is_interrupted(row: sqlite3.Row, server_run_id: str) -> bool:
    """A RUNNING row stamped by a process that is no longer here is provably
    orphaned. Derived at read time, never stored (design 5.3)."""
    return (row["step_state"] == StepState.RUNNING
            and row["server_run_id"] is not None
            and row["server_run_id"] != server_run_id)


def list_projects(conn, user_id: str, *, server_run_id: str) -> list[ProjectListItem]:
    rows = conn.execute(
        "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC, rowid DESC",
        (user_id,),
    ).fetchall()
    items = []
    for row in rows:
        interrupted = is_interrupted(row, server_run_id)
        items.append(ProjectListItem(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            status=row["status"],
            current_step=current_step(row["status"]),
            display_status=display_status(row["status"], row["step_state"]),
            needs_attention=needs_attention(row["step_state"], interrupted),
            is_interrupted=interrupted,
            completed_steps=completed_steps(row["status"]),
        ))
    return items
