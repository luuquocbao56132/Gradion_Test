import pytest

from app import db, pipeline, store
from app.steps import ProjectStatus, StepState

BOOK = "Chapter 1. The river bank."


@pytest.fixture
async def signed_in(aclient):
    await aclient.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return aclient


async def new_project(client, title="Willows"):
    response = await client.post("/api/projects", json={"title": title, "book_text": BOOK})
    return response.json()["id"]


async def test_running_the_current_step_returns_202_with_the_running_state(
        signed_in, fake_gemini):
    pid = await new_project(signed_in)
    fake_gemini.hold_from(0)

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})

    assert response.status_code == 202
    project = response.json()["project"]
    assert project["step_state"] == "RUNNING"
    assert project["current_step"] == "STYLE"
    assert project["display_status"] == "In progress"
    assert project["is_interrupted"] is False

    fake_gemini.release()
    await pipeline.drain_tasks()


async def test_the_step_completes_and_advances_the_status(signed_in):
    pid = await new_project(signed_in)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await pipeline.drain_tasks()

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["status"] == "STYLE_SET"
    assert project["step_state"] == "IDLE"
    assert project["current_step"] == "CHARACTERS"
    assert project["completed_steps"] == 1
    assert project["style_text"]


async def test_a_future_step_is_409_and_makes_zero_gemini_calls(signed_in, fake_gemini):
    """Step ordering, and it costs nothing to enforce (assessment 4.3)."""
    pid = await new_project(signed_in)

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "PORTRAITS"})

    assert response.status_code == 409
    body = response.json()
    assert body["project"]["status"] == "CREATED"
    assert body["project"]["step_state"] == "IDLE"
    assert body["error"]["code"] == "CONFLICT"
    assert fake_gemini.calls == []


async def test_an_already_completed_step_is_409(signed_in):
    pid = await new_project(signed_in)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await pipeline.drain_tasks()

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    assert response.status_code == 409


async def test_a_second_run_while_one_is_in_flight_is_409_and_adds_no_calls(
        signed_in, fake_gemini):
    pid = await new_project(signed_in)
    fake_gemini.hold_from(0)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await fake_gemini.wait_for_calls(1)
    calls_before = len(fake_gemini.calls)

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})

    assert response.status_code == 409
    assert response.json()["project"]["step_state"] == "RUNNING"
    assert len(fake_gemini.calls) == calls_before

    fake_gemini.release()
    await pipeline.drain_tasks()


async def test_a_failure_records_the_step_as_failed_with_a_user_safe_message(
        signed_in, fake_gemini, settings):
    from app.gemini.protocol import GeminiError
    pid = await new_project(signed_in)
    fake_gemini.fail_on(0, GeminiError("upstream refused"))

    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await pipeline.drain_tasks()

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["status"] == "CREATED"          # never advanced
    assert project["failure"]["code"] == "GEMINI_ERROR"
    assert project["needs_attention"] is True
    assert project["display_status"] == "In progress"


async def test_a_202_never_becomes_a_500_when_the_step_fails_later(signed_in, fake_gemini):
    """POST /run is finished at 202; a Gemini failure 30 seconds later surfaces
    as a later project view, not retroactively as an HTTP error (design 8.2)."""
    from app.gemini.protocol import GeminiError
    pid = await new_project(signed_in)
    fake_gemini.fail_on(0, GeminiError("boom"))

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    assert response.status_code == 202

    await pipeline.drain_tasks()
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["step_state"] == "FAILED"


async def test_running_another_users_project_is_404(aclient):
    await aclient.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = await new_project(aclient)
    await aclient.delete("/api/session")
    await aclient.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})

    response = await aclient.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    assert response.status_code == 404


async def test_an_unknown_step_name_is_422(signed_in):
    pid = await new_project(signed_in)
    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "SOUNDTRACK"})
    assert response.status_code == 422
