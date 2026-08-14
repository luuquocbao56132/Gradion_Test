import asyncio

import httpx
import pytest

from app import pipeline

BOOK = "Chapter 1. The river bank."
STYLE_CALL_KINDS = ["upload", "text", "text"]


@pytest.fixture
async def signed_in(aclient):
    await aclient.post(
        "/api/session", json={"name": "Ada", "email": "ada@example.com"}
    )
    return aclient


async def create(client):
    response = await client.post(
        "/api/projects", json={"title": "Willows", "book_text": BOOK}
    )
    return response.json()["id"]


async def test_two_simultaneous_runs_produce_one_202_one_409_and_one_execution(
    signed_in, fake_gemini
):
    """A real event-loop race, not two sequential requests (assessment 4.3)."""
    pid = await create(signed_in)
    fake_gemini.hold_from(0)

    try:
        first, second = await asyncio.gather(
            signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"}),
            signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"}),
        )

        assert sorted([first.status_code, second.status_code]) == [202, 409]
        loser = first if first.status_code == 409 else second
        assert loser.json()["project"]["step_state"] == "RUNNING"
        assert loser.json()["project"]["current_step"] == "STYLE"
        assert loser.json()["project"]["is_interrupted"] is False
        await fake_gemini.wait_for_calls(1)
        await asyncio.sleep(0)
        assert [call.kind for call in fake_gemini.calls] == ["upload"]
    finally:
        fake_gemini.release()
        await pipeline.drain_tasks()

    assert [call.kind for call in fake_gemini.calls] == STYLE_CALL_KINDS
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["status"] == "STYLE_SET"


async def test_ten_simultaneous_runs_still_produce_exactly_one_execution(
    signed_in, fake_gemini
):
    pid = await create(signed_in)
    fake_gemini.hold_from(0)

    try:
        responses = await asyncio.gather(
            *[
                signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
                for _ in range(10)
            ]
        )

        assert [response.status_code for response in responses].count(202) == 1
        losers = [response for response in responses if response.status_code == 409]
        assert len(losers) == 9
        assert all(
            response.json()["project"]["step_state"] == "RUNNING"
            for response in losers
        )
        assert all(
            response.json()["project"]["current_step"] == "STYLE"
            for response in losers
        )
        assert all(
            response.json()["project"]["is_interrupted"] is False
            for response in losers
        )
        await fake_gemini.wait_for_calls(1)
        await asyncio.sleep(0)
        assert [call.kind for call in fake_gemini.calls] == ["upload"]
    finally:
        fake_gemini.release()
        await pipeline.drain_tasks()

    assert [call.kind for call in fake_gemini.calls] == STYLE_CALL_KINDS
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["status"] == "STYLE_SET"


async def test_a_refresh_mid_step_shows_the_in_flight_state_and_starts_nothing(
    signed_in, fake_gemini
):
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    try:
        response = await signed_in.post(
            f"/api/projects/{pid}/run", json={"step": "STYLE"}
        )
        assert response.status_code == 202
        await fake_gemini.wait_for_calls(1)

        for _ in range(3):
            project = (await signed_in.get(f"/api/projects/{pid}")).json()
            assert project["step_state"] == "RUNNING"
            assert project["current_step"] == "STYLE"
            assert project["is_interrupted"] is False

        await asyncio.sleep(0)
        assert [call.kind for call in fake_gemini.calls] == ["upload"]
    finally:
        fake_gemini.release()
        await pipeline.drain_tasks()

    assert [call.kind for call in fake_gemini.calls] == STYLE_CALL_KINDS
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["status"] == "STYLE_SET"


async def test_a_second_client_sees_the_same_in_flight_run_and_starts_nothing(
    app, signed_in, fake_gemini
):
    """A second tab: a different HTTP client carrying the same session cookie."""
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    try:
        response = await signed_in.post(
            f"/api/projects/{pid}/run", json={"step": "STYLE"}
        )
        assert response.status_code == 202
        await fake_gemini.wait_for_calls(1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            cookies=signed_in.cookies,
        ) as second_tab:
            project = (await second_tab.get(f"/api/projects/{pid}")).json()
            assert project["step_state"] == "RUNNING"
            assert project["current_step"] == "STYLE"
            conflict = await second_tab.post(
                f"/api/projects/{pid}/run", json={"step": "STYLE"}
            )
            assert conflict.status_code == 409
            assert conflict.json()["project"] == project

        await asyncio.sleep(0)
        assert [call.kind for call in fake_gemini.calls] == ["upload"]
    finally:
        fake_gemini.release()
        await pipeline.drain_tasks()

    assert [call.kind for call in fake_gemini.calls] == STYLE_CALL_KINDS
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["status"] == "STYLE_SET"


async def test_signing_out_and_back_in_restores_results_and_regenerates_nothing(
    signed_in, fake_gemini
):
    pid = await create(signed_in)
    for step in ["STYLE", "CHARACTERS", "PORTRAITS"]:
        response = await signed_in.post(
            f"/api/projects/{pid}/run", json={"step": step}
        )
        assert response.status_code == 202
        await pipeline.drain_tasks()

    before = (await signed_in.get(f"/api/projects/{pid}")).json()
    calls_before = [call.kind for call in fake_gemini.calls]
    assert calls_before == [
        "upload",
        "text",
        "text",
        "structured",
        "image",
        "image",
        "image",
    ]

    await signed_in.delete("/api/session")
    await signed_in.post(
        "/api/session", json={"name": "Ada", "email": "ada@example.com"}
    )

    listed = (await signed_in.get("/api/projects")).json()
    assert [project["id"] for project in listed] == [pid]
    after = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert after == before
    await asyncio.sleep(0)
    await pipeline.drain_tasks()
    assert [call.kind for call in fake_gemini.calls] == calls_before


async def test_the_book_stays_readable_at_every_stage_of_the_pipeline(signed_in):
    """Assessment 4.4: generated state must never hide the source book."""
    pid = await create(signed_in)
    phases = [
        ("STYLE", "STYLE_SET"),
        ("CHARACTERS", "CHARACTERS_GENERATED"),
        ("PORTRAITS", "PORTRAITS_GENERATED"),
        ("CHAPTERS", "CHAPTERS_GENERATED"),
        ("ILLUSTRATIONS", "DONE"),
    ]
    for step, expected_status in phases:
        assert (await signed_in.get(f"/api/projects/{pid}/book")).json()["text"] == BOOK
        response = await signed_in.post(
            f"/api/projects/{pid}/run", json={"step": step}
        )
        assert response.status_code == 202
        await pipeline.drain_tasks()
        project = (await signed_in.get(f"/api/projects/{pid}")).json()
        assert project["status"] == expected_status
    assert (await signed_in.get(f"/api/projects/{pid}/book")).json()["text"] == BOOK
