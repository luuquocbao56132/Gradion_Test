from __future__ import annotations

import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

from app.models import EntityView, Failure, ProjectListItem, ProjectView
from app.steps import (
    MAX_CHAPTERS,
    MAX_CHARACTERS,
    ProjectStatus,
    StepName,
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


# --------------------------------------------------------------------------
# Read model
# --------------------------------------------------------------------------

def list_characters(conn, project_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM characters WHERE project_id = ? ORDER BY position", (project_id,)
    ).fetchall()


def list_chapters(conn, project_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chapters WHERE project_id = ? ORDER BY position", (project_id,)
    ).fetchall()


def _entity_views(rows, *, project_id: str, path_column: str, url_suffix: str,
                  collection: str, generating: bool) -> list[EntityView]:
    """Per-item state, derived. The handler iterates in position order, so the
    first item still missing its artifact is the one in flight (design 4.5)."""
    seen_missing = False
    views: list[EntityView] = []
    for row in rows:
        path = row[path_column]
        if path is not None:
            state = "ready"
            url = f"/api/projects/{project_id}/{collection}/{row['id']}/{url_suffix}"
        else:
            url = None
            if generating and not seen_missing:
                state = "generating"
                seen_missing = True
            else:
                state = "pending"
        views.append(EntityView(id=row["id"], position=row["position"], name=row["name"],
                                prompt=row["prompt"], image_url=url, image_state=state))
    return views


def read_project_view(conn, project_id: str, user_id: str, *,
                      server_run_id: str) -> ProjectView | None:
    row = get_project(conn, project_id, user_id)
    if row is None:
        return None

    interrupted = is_interrupted(row, server_run_id)
    running = row["step_state"] == StepState.RUNNING and not interrupted
    step = current_step(row["status"])

    failure = None
    if row["error_code"] is not None:
        failure = Failure(code=row["error_code"], message=row["error_message"] or "")

    return ProjectView(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        status=row["status"],
        step_state=row["step_state"],
        current_step=step,
        display_status=display_status(row["status"], row["step_state"]),
        needs_attention=needs_attention(row["step_state"], interrupted),
        is_interrupted=interrupted,
        completed_steps=completed_steps(row["status"]),
        style_text=row["style_text"],
        book_excerpt=row["book_excerpt"],
        failure=failure,
        characters=_entity_views(
            list_characters(conn, project_id), project_id=project_id,
            path_column="portrait_path", url_suffix="portrait", collection="characters",
            generating=running and step == StepName.PORTRAITS),
        chapters=_entity_views(
            list_chapters(conn, project_id), project_id=project_id,
            path_column="illustration_path", url_suffix="illustration", collection="chapters",
            generating=running and step == StepName.ILLUSTRATIONS),
    )


# ---- step outputs: artifact and chain head always move together ------------

def save_style(conn, project_id: str, *, style_text: str, text_interaction_id: str) -> None:
    conn.execute(
        "UPDATE projects SET style_text = ?, text_interaction_id = ? WHERE id = ?",
        (style_text, text_interaction_id, project_id),
    )


def _replace_children(conn, table: str, project_id: str,
                      items: list[tuple[str, str]], text_interaction_id: str) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
        for position, (name, prompt) in enumerate(items):
            conn.execute(
                f"INSERT INTO {table} (id, project_id, position, name, prompt) "
                "VALUES (?,?,?,?,?)",
                (new_id(), project_id, position, name, prompt),
            )
        conn.execute("UPDATE projects SET text_interaction_id = ? WHERE id = ?",
                     (text_interaction_id, project_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def save_characters(conn, project_id: str, items: list[tuple[str, str]], *,
                    text_interaction_id: str) -> None:
    _replace_children(conn, "characters", project_id, items, text_interaction_id)


def save_chapters(conn, project_id: str, items: list[tuple[str, str]], *,
                  text_interaction_id: str) -> None:
    _replace_children(conn, "chapters", project_id, items, text_interaction_id)


def _save_artifact(conn, *, table: str, column: str, project_id: str, row_id: str,
                   path: str, image_interaction_id: str) -> None:
    """One transaction. Saving the file without the head would make a retry
    re-seed a chain that has diverged from the images on disk; saving the head
    without the file would make the handler skip an artifact it does not have
    (design 7.2)."""
    conn.execute("BEGIN")
    try:
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (path, row_id))
        conn.execute("UPDATE projects SET image_interaction_id = ? WHERE id = ?",
                     (image_interaction_id, project_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def save_portrait(conn, *, project_id: str, character_id: str, portrait_path: str,
                  image_interaction_id: str) -> None:
    _save_artifact(conn, table="characters", column="portrait_path", project_id=project_id,
                   row_id=character_id, path=portrait_path,
                   image_interaction_id=image_interaction_id)


def save_illustration(conn, *, project_id: str, chapter_id: str, illustration_path: str,
                      image_interaction_id: str) -> None:
    _save_artifact(conn, table="chapters", column="illustration_path", project_id=project_id,
                   row_id=chapter_id, path=illustration_path,
                   image_interaction_id=image_interaction_id)
