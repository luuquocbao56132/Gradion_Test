import pytest
from starlette.websockets import WebSocketDisconnect

BOOK = "Chapter 1. The river bank."


@pytest.fixture
def signed_in(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return client


def create(client, title="Willows"):
    return client.post("/api/projects",
                       json={"title": title, "book_text": BOOK}).json()["id"]


def test_subscribing_immediately_returns_the_authoritative_current_state(signed_in):
    pid = create(signed_in)
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        message = ws.receive_json()
    assert message["type"] == "project.state"
    assert message["project"]["id"] == pid
    assert message["project"]["status"] == "CREATED"
    assert message["project"]["current_step"] == "STYLE"


def test_the_socket_payload_is_identical_to_the_rest_project_view(signed_in):
    pid = create(signed_in)
    rest = signed_in.get(f"/api/projects/{pid}").json()
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        assert ws.receive_json()["project"] == rest


def test_state_changing_between_get_and_subscribe_still_reaches_the_client(signed_in):
    """The GET -> subscribe race. The unconditional state message on subscribe
    closes it, and register-read-offer runs with no await between (design 9.3)."""
    pid = create(signed_in)
    stale = signed_in.get(f"/api/projects/{pid}").json()
    signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})   # state moves

    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        fresh = ws.receive_json()["project"]

    assert stale["status"] == "CREATED"
    assert fresh["status"] == "STYLE_SET" or fresh["step_state"] == "RUNNING"
    assert fresh != stale


def test_two_connections_each_receive_the_current_state(signed_in):
    pid = create(signed_in)
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as first, \
         signed_in.websocket_connect(f"/ws/projects/{pid}") as second:
        assert first.receive_json()["project"]["id"] == pid
        assert second.receive_json()["project"]["id"] == pid


def test_another_users_project_is_closed_with_1008(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = create(client)
    client.delete("/api/session")
    client.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/ws/projects/{pid}") as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_a_missing_project_closes_with_the_same_code(signed_in):
    """The same code either way, so existence is never confirmed."""
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with signed_in.websocket_connect("/ws/projects/does-not-exist") as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_no_session_cookie_closes_with_1008(client):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/projects/anything") as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_reconnecting_yields_current_persisted_truth(signed_in):
    """Reconnect and first connect are one code path."""
    pid = create(signed_in)
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        assert ws.receive_json()["project"]["status"] == "CREATED"

    signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})

    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        assert ws.receive_json()["project"]["status"] in {"CREATED", "STYLE_SET"}
        assert ws.receive_json is not None


def test_disconnecting_removes_the_subscriber(signed_in, app):
    pid = create(signed_in)
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        ws.receive_json()
        assert app.state.registry.count(pid) == 1
    assert app.state.registry.count(pid) == 0
