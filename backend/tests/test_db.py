import sqlite3

import pytest

from app import db


@pytest.fixture
def ready(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as conn:
        db.init_schema(conn)
    return settings


def test_connection_applies_the_three_required_pragmas(ready):
    with db.get_conn(ready) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000


def test_rows_are_accessible_by_column_name(ready):
    with db.get_conn(ready) as conn:
        conn.execute(
            "INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,?)",
            ("u1", "a@b.c", "Ada", "2026-08-14T00:00:00+00:00"),
        )
        assert conn.execute("SELECT * FROM users").fetchone()["email"] == "a@b.c"


def test_init_schema_creates_every_table(ready):
    with db.get_conn(ready) as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "sessions", "projects", "characters", "chapters"} <= names


def test_init_schema_is_safe_to_run_twice(ready):
    with db.get_conn(ready) as conn:
        db.init_schema(conn)


def test_foreign_keys_are_enforced(ready):
    with db.get_conn(ready) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
                ("t", "no-such-user", "2026-08-14T00:00:00+00:00"),
            )
