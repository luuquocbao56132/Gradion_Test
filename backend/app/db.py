from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import Settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  id          TEXT PRIMARY KEY,
  email       TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token       TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id                   TEXT PRIMARY KEY,
  user_id              TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title                TEXT NOT NULL,
  created_at           TEXT NOT NULL,
  book_path            TEXT NOT NULL,
  book_excerpt         TEXT NOT NULL,
  status               TEXT NOT NULL DEFAULT 'CREATED',
  step_state           TEXT NOT NULL DEFAULT 'IDLE',
  step_started_at      TEXT,
  server_run_id        TEXT,
  error_code           TEXT,
  error_message        TEXT,
  style_text           TEXT,
  text_interaction_id  TEXT,
  image_interaction_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS characters (
  id             TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  position       INTEGER NOT NULL,
  name           TEXT NOT NULL,
  prompt         TEXT NOT NULL,
  portrait_path  TEXT,
  UNIQUE (project_id, position)
);

CREATE TABLE IF NOT EXISTS chapters (
  id                 TEXT PRIMARY KEY,
  project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  position           INTEGER NOT NULL,
  name               TEXT NOT NULL,
  prompt             TEXT NOT NULL,
  illustration_path  TEXT,
  UNIQUE (project_id, position)
);
"""


@contextmanager
def get_conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    """A short-lived connection with the required pragmas applied.

    journal_mode lives in the database file, but busy_timeout and foreign_keys
    are per-connection and must be set every time (design 4.4).
    """
    conn = sqlite3.connect(settings.db_path, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
