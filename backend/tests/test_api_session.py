def test_a_new_email_creates_a_user_and_sets_a_session_cookie(client):
    response = client.post("/api/session", json={"name": "Ada", "email": "Ada@Example.com "})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"      # normalised: trimmed, lowercased
    assert body["name"] == "Ada"
    assert "session" in response.cookies


def test_the_same_email_returns_the_same_user(client):
    first = client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"}).json()
    second = client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"}).json()
    assert first["user_id"] == second["user_id"]


def test_signing_in_again_updates_the_stored_name(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    client.post("/api/session", json={"name": "Ada Lovelace", "email": "ada@example.com"})
    assert client.get("/api/session").json()["name"] == "Ada Lovelace"


def test_get_session_restores_identity_on_app_boot(client):
    created = client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"}).json()
    restored = client.get("/api/session")
    assert restored.status_code == 200
    assert restored.json()["user_id"] == created["user_id"]


def test_get_session_without_a_cookie_is_401(client):
    assert client.get("/api/session").status_code == 401


def test_sign_out_revokes_the_session(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    assert client.delete("/api/session").status_code == 204
    assert client.get("/api/session").status_code == 401


def test_a_stale_cookie_is_401_not_a_crash(client):
    client.cookies.set("session", "no-such-token")
    assert client.get("/api/session").status_code == 401


def test_a_blank_name_is_rejected(client):
    assert client.post("/api/session", json={"name": "  ", "email": "a@b.co"}).status_code == 422


def test_a_malformed_email_is_rejected(client):
    assert client.post("/api/session", json={"name": "Ada", "email": "not-an-email"}
                       ).status_code == 422


def test_the_session_cookie_is_httponly_and_lax(client):
    response = client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
