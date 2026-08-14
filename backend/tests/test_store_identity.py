import pytest

from app import db, store


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as c:
        db.init_schema(c)
        yield c


def test_a_new_email_creates_a_user(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    row = store.get_user(conn, user_id)
    assert (row["email"], row["name"]) == ("ada@example.com", "Ada")


def test_a_returning_email_reuses_the_same_user_row(conn):
    first = store.upsert_user(conn, email="ada@example.com", name="Ada")
    second = store.upsert_user(conn, email="ada@example.com", name="Ada")
    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_signing_in_again_updates_the_stored_name(conn):
    """Matches app-demo.html:347 - the demo overwrites the name on re-entry."""
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    store.upsert_user(conn, email="ada@example.com", name="Ada Lovelace")
    assert store.get_user(conn, user_id)["name"] == "Ada Lovelace"


def test_sessions_resolve_to_their_user_and_tokens_are_unguessable(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    token = store.create_session(conn, user_id)
    assert len(token) >= 32
    assert store.user_for_session(conn, token)["id"] == user_id


def test_an_unknown_token_resolves_to_nothing(conn):
    assert store.user_for_session(conn, "not-a-token") is None


def test_deleting_a_session_revokes_it(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    token = store.create_session(conn, user_id)
    store.delete_session(conn, token)
    assert store.user_for_session(conn, token) is None
