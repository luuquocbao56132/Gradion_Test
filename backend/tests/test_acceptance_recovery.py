import asyncio

import pytest

from app import db, pipeline, store
from app.gemini.protocol import GeminiError, InteractionNotFound

BOOK = "Chapter 1. The river bank."


@pytest.fixture
async def signed_in(aclient):
    await aclient.post(
        "/api/session", json={"name": "Ada", "email": "ada@example.com"}
    )
    return aclient


async def create(client):
    return (
        await client.post(
            "/api/projects", json={"title": "Willows", "book_text": BOOK}
        )
    ).json()["id"]


async def run(client, pid, step):
    response = await client.post(f"/api/projects/{pid}/run", json={"step": step})
    await pipeline.drain_tasks()
    return response


async def advance(client, pid, steps):
    for step in steps:
        await run(client, pid, step)


# --------------------------------------------------------------------------
# A later step failing preserves everything before it
# --------------------------------------------------------------------------


async def test_a_late_failure_leaves_every_earlier_output_intact(
    signed_in, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    fake_gemini.fail_on(
        len(fake_gemini.calls), GeminiError("image service refused")
    )

    await run(signed_in, pid, "PORTRAITS")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["status"] == "CHARACTERS_GENERATED"
    assert project["style_text"]
    assert len(project["characters"]) == 2
    assert project["failure"]["code"] == "GEMINI_ERROR"


async def test_retrying_touches_only_the_failed_step(signed_in, fake_gemini):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    fake_gemini.fail_on(len(fake_gemini.calls), GeminiError("boom"))
    await run(signed_in, pid, "PORTRAITS")

    before = list(fake_gemini.calls)
    await run(signed_in, pid, "PORTRAITS")

    retried = fake_gemini.calls[len(before) :]
    assert all(call.kind == "image" for call in retried)
    assert not any(call.kind == "upload" for call in retried)
    assert (
        await signed_in.get(f"/api/projects/{pid}")
    ).json()["status"] == "PORTRAITS_GENERATED"


async def test_portrait_one_survives_a_portrait_two_failure(
    signed_in, fake_gemini, settings
):
    """Never losing generated results (assessment 4.3)."""
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    # seed, portrait 1, then fail portrait 2
    fake_gemini.fail_on(len(fake_gemini.calls) + 2, GeminiError("dropped"))

    await run(signed_in, pid, "PORTRAITS")
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["characters"][0]["image_state"] == "ready"
    assert project["characters"][1]["image_state"] == "pending"

    first_prompt = project["characters"][0]["prompt"]
    before = len(fake_gemini.calls)
    await run(signed_in, pid, "PORTRAITS")

    retried = fake_gemini.calls[before:]
    assert not any(first_prompt in (call.prompt or "") for call in retried)
    assert len([call for call in retried if call.kind == "image"]) == 1
    after = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert all(character["image_state"] == "ready" for character in after["characters"])
    assert after["status"] == "PORTRAITS_GENERATED"


# --------------------------------------------------------------------------
# Interruption: a RUNNING row from a process that is gone
# --------------------------------------------------------------------------


async def test_a_run_stamped_by_a_dead_process_surfaces_as_interrupted(
    signed_in, settings, app
):
    pid = await create(signed_in)
    with db.get_conn(settings) as conn:
        conn.execute(
            "UPDATE projects SET step_state='RUNNING', server_run_id='old-process' "
            "WHERE id=?",
            (pid,),
        )

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["is_interrupted"] is True
    assert project["needs_attention"] is True
    assert project["step_state"] == "RUNNING"
    assert project["display_status"] == "In progress"


async def test_the_normal_retry_command_recovers_an_interrupted_step(
    signed_in, settings, fake_gemini
):
    """Recovery is not a separate endpoint - retrying IS the recovery."""
    pid = await create(signed_in)
    with db.get_conn(settings) as conn:
        conn.execute(
            "UPDATE projects SET step_state='RUNNING', server_run_id='old-process' "
            "WHERE id=?",
            (pid,),
        )

    assert (await run(signed_in, pid, "STYLE")).status_code == 202

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["status"] == "STYLE_SET"
    assert project["is_interrupted"] is False
    assert project["needs_attention"] is False


async def test_prior_outputs_survive_an_interruption_and_recovery(
    signed_in, settings
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    with db.get_conn(settings) as conn:
        conn.execute(
            "UPDATE projects SET step_state='RUNNING', server_run_id='old-process' "
            "WHERE id=?",
            (pid,),
        )

    await run(signed_in, pid, "PORTRAITS")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["style_text"] and len(project["characters"]) == 2
    assert project["status"] == "PORTRAITS_GENERATED"


# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------


async def test_a_cancelled_task_leaves_the_step_failed_never_running(
    signed_in, settings, fake_gemini
):
    """The one stuck-forever shape server_run_id alone does not answer
    (design 6.3)."""
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    try:
        await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
        await fake_gemini.wait_for_calls(1)

        task = next(iter(pipeline._TASKS))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        project = (await signed_in.get(f"/api/projects/{pid}")).json()
        assert project["step_state"] == "FAILED"
        assert project["failure"]["message"] == pipeline.CANCELLED_MESSAGE
        assert project["is_interrupted"] is False
    finally:
        fake_gemini.release()
        await pipeline.drain_tasks()


async def test_a_cancelled_step_is_retryable_and_completes(signed_in, fake_gemini):
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    try:
        await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
        await fake_gemini.wait_for_calls(1)
        task = next(iter(pipeline._TASKS))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        fake_gemini.release()
        await pipeline.drain_tasks()

    await run(signed_in, pid, "STYLE")
    assert (
        await signed_in.get(f"/api/projects/{pid}")
    ).json()["status"] == "STYLE_SET"


async def test_a_cancellation_arriving_after_takeover_writes_nothing(
    signed_in, settings, fake_gemini
):
    """A late cancellation cannot clobber a newer execution."""
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    try:
        await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
        await fake_gemini.wait_for_calls(1)
        task = next(iter(pipeline._TASKS))

        with db.get_conn(settings) as conn:
            assert (
                store.begin_step(
                    conn,
                    pid,
                    expected_status="CREATED",
                    server_run_id="run-Z",
                    now=store.now_iso(),
                )
                is True
            )

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        with db.get_conn(settings) as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id=?", (pid,)
            ).fetchone()
        assert row["step_state"] == "RUNNING"
        assert row["server_run_id"] == "run-Z"
    finally:
        fake_gemini.release()
        await pipeline.drain_tasks()


# --------------------------------------------------------------------------
# Provider-side context expiry
# --------------------------------------------------------------------------


async def test_expiry_fails_with_one_attempt_and_nulls_the_head_that_raised(
    signed_in, settings, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE"])
    before = len(fake_gemini.calls)
    fake_gemini.fail_on(before, InteractionNotFound("interaction expired"))

    await run(signed_in, pid, "CHARACTERS")

    assert len(fake_gemini.calls) == before + 1
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["failure"]["code"] == "GEMINI_ERROR"
    assert "expired" in project["failure"]["message"]
    with db.get_conn(settings) as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["text_interaction_id"] is None
    assert row["style_text"] is not None


async def test_the_user_retry_rebuilds_from_minimum_persisted_state(
    signed_in, settings, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE"])
    fake_gemini.fail_on(
        len(fake_gemini.calls), InteractionNotFound("expired")
    )
    await run(signed_in, pid, "CHARACTERS")

    before = len(fake_gemini.calls)
    await run(signed_in, pid, "CHARACTERS")

    retried = fake_gemini.calls[before:]
    assert [call.kind for call in retried] == ["upload", "structured"]
    assert retried[1].previous_interaction_id is None
    assert (
        await signed_in.get(f"/api/projects/{pid}")
    ).json()["status"] == "CHARACTERS_GENERATED"


async def test_image_chain_expiry_nulls_only_the_image_head(
    signed_in, settings, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    fake_gemini.fail_on(
        len(fake_gemini.calls), InteractionNotFound("expired")
    )

    await run(signed_in, pid, "PORTRAITS")

    with db.get_conn(settings) as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["image_interaction_id"] is None
    assert row["text_interaction_id"] is not None


async def test_steps_three_and_five_never_re_upload_the_book_on_recovery(
    signed_in, settings, fake_gemini
):
    pid = await create(signed_in)
    await advance(
        signed_in,
        pid,
        ["STYLE", "CHARACTERS", "PORTRAITS", "CHAPTERS"],
    )
    with db.get_conn(settings) as conn:
        conn.execute(
            "UPDATE projects SET image_interaction_id = NULL WHERE id=?", (pid,)
        )

    before = len(fake_gemini.calls)
    await run(signed_in, pid, "ILLUSTRATIONS")

    assert not any(call.kind == "upload" for call in fake_gemini.calls[before:])
    assert (
        await signed_in.get(f"/api/projects/{pid}")
    ).json()["status"] == "DONE"


# --------------------------------------------------------------------------
# Cost discipline
# --------------------------------------------------------------------------


async def test_a_provider_failure_is_attempted_once_and_never_looped(
    signed_in, fake_gemini
):
    """Assessment 4.3: never auto-retry a Gemini call in a loop."""
    pid = await create(signed_in)
    fake_gemini.fail_on(0, GeminiError("rate limited"))

    await run(signed_in, pid, "STYLE")

    assert len(fake_gemini.calls) == 1
    assert (
        await signed_in.get(f"/api/projects/{pid}")
    ).json()["step_state"] == "FAILED"


async def test_an_over_cap_response_surfaces_as_invalid_output(
    signed_in, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE"])
    fake_gemini.extra_items = 3

    await run(signed_in, pid, "CHARACTERS")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["failure"]["code"] == "INVALID_OUTPUT"
    assert project["characters"] == []
    assert project["status"] == "STYLE_SET"
