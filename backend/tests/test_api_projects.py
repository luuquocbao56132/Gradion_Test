import pytest

from app import db, store

BOOK = "Once upon a time, in a small burrow by the river, there lived a Mole."


@pytest.fixture
def signed_in(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return client


def make_project(c, title="Willows", book=BOOK):
    return c.post("/api/projects", json={"title": title, "book_text": book})


def test_creating_a_project_persists_the_row_and_the_book_file(signed_in, settings, fake_gemini):
    response = make_project(signed_in)

    assert response.status_code == 201
    project = response.json()
    assert project["title"] == "Willows"
    assert project["status"] == "CREATED"
    assert project["display_status"] == "Draft"
    assert project["current_step"] == "STYLE"
    assert project["book_excerpt"].startswith("Once upon a time")

    book_file = settings.data_dir / "projects" / project["id"] / "book.txt"
    assert book_file.read_text(encoding="utf-8") == BOOK


def test_creating_a_project_makes_zero_gemini_calls(signed_in, fake_gemini):
    """The upload happens lazily inside step 1, so creation cannot fail on a
    provider error and an unopened project never holds a dead file URI."""
    make_project(signed_in)
    assert fake_gemini.calls == []


def test_creation_requires_a_title_and_book_text(signed_in):
    assert make_project(signed_in, title="   ").status_code == 422
    assert make_project(signed_in, book="").status_code == 422


def test_creation_requires_a_session(client):
    assert make_project(client).status_code == 401


def test_the_list_shows_only_this_users_projects(client, settings):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    make_project(client, title="Mine")
    client.delete("/api/session")
    client.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})
    make_project(client, title="Theirs")

    titles = [p["title"] for p in client.get("/api/projects").json()]
    assert titles == ["Theirs"]


def test_signing_out_and_back_in_restores_the_same_projects(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    created = make_project(client).json()
    client.delete("/api/session")
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})

    listed = client.get("/api/projects").json()
    assert [p["id"] for p in listed] == [created["id"]]
    assert client.get(f"/api/projects/{created['id']}").status_code == 200


def test_the_empty_list_is_an_empty_array_not_an_error(signed_in):
    assert signed_in.get("/api/projects").json() == []


def test_the_detail_view_is_the_full_project(signed_in):
    pid = make_project(signed_in).json()["id"]
    detail = signed_in.get(f"/api/projects/{pid}").json()
    assert detail["characters"] == [] and detail["chapters"] == []
    assert detail["failure"] is None
    assert detail["completed_steps"] == 0


def test_the_book_is_readable_in_full_at_any_point_in_the_pipeline(signed_in):
    """Assessment 4.4. Kept out of the project view because it can be 230 KB
    and never changes, so every state payload stays small (design 8)."""
    pid = make_project(signed_in).json()["id"]
    assert signed_in.get(f"/api/projects/{pid}/book").json()["text"] == BOOK


def test_another_users_project_is_404_not_403(client):
    """Do not confirm existence (design 8.2)."""
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = make_project(client).json()["id"]
    client.delete("/api/session")
    client.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})

    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get(f"/api/projects/{pid}/book").status_code == 404


def test_artifact_bytes_are_served_and_ownership_checked(client, settings):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = make_project(client).json()["id"]
    with db.get_conn(settings) as conn:
        store.save_characters(conn, pid, [("Toad", "a toad")], text_interaction_id="i")
        cid = store.list_characters(conn, pid)[0]["id"]
        path = (settings.data_dir / "projects" / pid / "portraits" / f"{cid}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\nportrait")
        store.save_portrait(conn, project_id=pid, character_id=cid,
                            portrait_path=f"projects/{pid}/portraits/{cid}.png",
                            image_interaction_id="i-img")

    served = client.get(f"/api/projects/{pid}/characters/{cid}/portrait")
    assert served.status_code == 200
    assert served.content == b"\x89PNG\r\n\x1a\nportrait"
    assert served.headers["content-type"] == "image/png"

    client.delete("/api/session")
    client.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})
    assert client.get(f"/api/projects/{pid}/characters/{cid}/portrait").status_code == 404


def test_an_ungenerated_portrait_is_404(client, settings):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = make_project(client).json()["id"]
    with db.get_conn(settings) as conn:
        store.save_characters(conn, pid, [("Toad", "a toad")], text_interaction_id="i")
        cid = store.list_characters(conn, pid)[0]["id"]
    assert client.get(f"/api/projects/{pid}/characters/{cid}/portrait").status_code == 404
