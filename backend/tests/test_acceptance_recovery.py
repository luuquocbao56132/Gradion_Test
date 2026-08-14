import asyncio

import pytest

from app import db, pipeline, store
from app.gemini import prompts
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


def earlier_output_snapshot(project):
    return {
        "style_text": project["style_text"],
        "characters": [
            {
                key: character[key]
                for key in ("id", "position", "name", "prompt")
            }
            for character in project["characters"]
        ],
    }


def state_snapshot(project):
    return {
        key: project[key]
        for key in ("status", "current_step", "completed_steps")
    }


def raw_heads(settings, pid):
    with db.get_conn(settings) as conn:
        row = conn.execute(
            "SELECT text_interaction_id, image_interaction_id "
            "FROM projects WHERE id=?",
            (pid,),
        ).fetchone()
    return row["text_interaction_id"], row["image_interaction_id"]


async def yield_and_drain():
    await asyncio.sleep(0)
    await pipeline.drain_tasks()


# --------------------------------------------------------------------------
# A later step failing preserves everything before it
# --------------------------------------------------------------------------


async def test_a_late_failure_leaves_every_earlier_output_intact(
    signed_in, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    before = (await signed_in.get(f"/api/projects/{pid}")).json()
    earlier_outputs = earlier_output_snapshot(before)
    earlier_state = state_snapshot(before)
    fake_gemini.fail_on(
        len(fake_gemini.calls), GeminiError("image service refused")
    )

    await run(signed_in, pid, "PORTRAITS")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert state_snapshot(project) == earlier_state
    assert earlier_output_snapshot(project) == earlier_outputs
    assert [character["image_state"] for character in project["characters"]] == [
        "pending",
        "pending",
    ]
    assert project["failure"]["code"] == "GEMINI_ERROR"


async def test_retrying_touches_only_the_failed_step(signed_in, fake_gemini):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    before_failure = (await signed_in.get(f"/api/projects/{pid}")).json()
    earlier_outputs = earlier_output_snapshot(before_failure)
    earlier_state = state_snapshot(before_failure)
    fake_gemini.fail_on(len(fake_gemini.calls), GeminiError("boom"))
    await run(signed_in, pid, "PORTRAITS")

    failed = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert earlier_output_snapshot(failed) == earlier_outputs
    assert state_snapshot(failed) == earlier_state

    before_retry = len(fake_gemini.calls)
    await run(signed_in, pid, "PORTRAITS")

    retried = fake_gemini.calls[before_retry:]
    assert [call.kind for call in retried] == ["image", "image", "image"]
    after = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert earlier_output_snapshot(after) == earlier_outputs
    assert [character["image_state"] for character in after["characters"]] == [
        "ready",
        "ready",
    ]
    assert after["status"] == "PORTRAITS_GENERATED"


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
    assert [call.kind for call in retried] == ["image"]
    assert first_prompt not in (retried[0].prompt or "")
    after = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert [character["image_state"] for character in after["characters"]] == [
        "ready",
        "ready",
    ]
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
    before = (await signed_in.get(f"/api/projects/{pid}")).json()
    earlier_outputs = earlier_output_snapshot(before)
    earlier_state = state_snapshot(before)
    with db.get_conn(settings) as conn:
        conn.execute(
            "UPDATE projects SET step_state='RUNNING', server_run_id='old-process' "
            "WHERE id=?",
            (pid,),
        )

    interrupted = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert interrupted["is_interrupted"] is True
    assert earlier_output_snapshot(interrupted) == earlier_outputs
    assert state_snapshot(interrupted) == earlier_state

    await run(signed_in, pid, "PORTRAITS")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert earlier_output_snapshot(project) == earlier_outputs
    assert [character["image_state"] for character in project["characters"]] == [
        "ready",
        "ready",
    ]
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


async def test_step_2_text_expiry_rebuilds_from_its_natural_null_image_head(
    signed_in, settings, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE"])
    text_head, image_head = raw_heads(settings, pid)
    assert text_head is not None
    assert image_head is None
    before_failure = len(fake_gemini.calls)
    fake_gemini.fail_on(
        before_failure, InteractionNotFound("interaction expired")
    )

    await run(signed_in, pid, "CHARACTERS")
    await yield_and_drain()

    failed_calls = fake_gemini.calls[before_failure:]
    assert [call.kind for call in failed_calls] == ["structured"]
    assert failed_calls[0].previous_interaction_id == text_head
    assert failed_calls[0].document_uri is None
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["failure"]["code"] == "GEMINI_ERROR"
    assert "expired" in project["failure"]["message"]
    assert raw_heads(settings, pid) == (None, None)

    before_retry = len(fake_gemini.calls)
    await run(signed_in, pid, "CHARACTERS")

    retry_calls = fake_gemini.calls[before_retry:]
    assert [call.kind for call in retry_calls] == ["upload", "structured"]
    assert retry_calls[1].previous_interaction_id is None
    assert retry_calls[1].document_uri == "files/fake-book.txt"
    assert (await signed_in.get(f"/api/projects/{pid}")).json()[
        "status"
    ] == "CHARACTERS_GENERATED"


async def test_step_4_text_expiry_preserves_the_natural_portrait_image_head(
    signed_in, settings, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS", "PORTRAITS"])
    before = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert [character["image_state"] for character in before["characters"]] == [
        "ready",
        "ready",
    ]
    text_head, image_head = raw_heads(settings, pid)
    assert text_head is not None
    assert image_head is not None
    before_failure = len(fake_gemini.calls)
    fake_gemini.fail_on(
        before_failure, InteractionNotFound("interaction expired")
    )

    await run(signed_in, pid, "CHAPTERS")
    await yield_and_drain()

    failed_calls = fake_gemini.calls[before_failure:]
    assert [call.kind for call in failed_calls] == ["structured"]
    assert failed_calls[0].previous_interaction_id == text_head
    assert failed_calls[0].document_uri is None
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["failure"]["code"] == "GEMINI_ERROR"
    assert raw_heads(settings, pid) == (None, image_head)

    before_retry = len(fake_gemini.calls)
    await run(signed_in, pid, "CHAPTERS")

    retry_calls = fake_gemini.calls[before_retry:]
    assert [call.kind for call in retry_calls] == ["upload", "structured"]
    assert retry_calls[1].previous_interaction_id is None
    assert retry_calls[1].document_uri == "files/fake-book.txt"
    assert (await signed_in.get(f"/api/projects/{pid}")).json()[
        "status"
    ] == "CHAPTERS_GENERATED"


async def test_step_3_image_expiry_keeps_portrait_one_and_reseeds_for_two(
    signed_in, settings, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    before = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert [character["image_state"] for character in before["characters"]] == [
        "pending",
        "pending",
    ]
    text_head, image_head = raw_heads(settings, pid)
    assert text_head is not None
    assert image_head is None
    first_prompt = before["characters"][0]["prompt"]
    second_prompt = before["characters"][1]["prompt"]
    before_failure = len(fake_gemini.calls)
    fake_gemini.fail_on(
        before_failure + 2, InteractionNotFound("interaction expired")
    )

    await run(signed_in, pid, "PORTRAITS")
    await yield_and_drain()

    failed_calls = fake_gemini.calls[before_failure:]
    assert [call.kind for call in failed_calls] == ["image", "image", "image"]
    assert failed_calls[0].previous_interaction_id is None
    assert failed_calls[0].reference_image_count == 0
    assert first_prompt in (failed_calls[1].prompt or "")
    assert second_prompt in (failed_calls[2].prompt or "")
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["failure"]["code"] == "GEMINI_ERROR"
    assert [character["image_state"] for character in project["characters"]] == [
        "ready",
        "pending",
    ]
    assert raw_heads(settings, pid) == (text_head, None)

    before_retry = len(fake_gemini.calls)
    await run(signed_in, pid, "PORTRAITS")

    retry_calls = fake_gemini.calls[before_retry:]
    assert [call.kind for call in retry_calls] == ["image", "image"]
    assert retry_calls[0].previous_interaction_id is None
    assert retry_calls[0].reference_image_count == 1
    assert retry_calls[1].previous_interaction_id is not None
    assert first_prompt not in (retry_calls[1].prompt or "")
    assert second_prompt in (retry_calls[1].prompt or "")
    assert all(call.kind != "upload" for call in retry_calls)
    retried = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert [character["image_state"] for character in retried["characters"]] == [
        "ready",
        "ready",
    ]
    assert retried["status"] == "PORTRAITS_GENERATED"
    final_text_head, final_image_head = raw_heads(settings, pid)
    assert final_text_head == text_head
    assert final_image_head is not None


async def test_step_5_image_expiry_preserves_text_and_retries_standalone(
    signed_in, settings, fake_gemini
):
    pid = await create(signed_in)
    await advance(
        signed_in,
        pid,
        ["STYLE", "CHARACTERS", "PORTRAITS", "CHAPTERS"],
    )
    before = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert [character["image_state"] for character in before["characters"]] == [
        "ready",
        "ready",
    ]
    assert [chapter["image_state"] for chapter in before["chapters"]] == [
        "pending"
    ]
    text_head, image_head = raw_heads(settings, pid)
    assert text_head is not None
    assert image_head is not None
    chapter = before["chapters"][0]
    before_failure = len(fake_gemini.calls)
    fake_gemini.fail_on(
        before_failure + 1, InteractionNotFound("interaction expired")
    )

    await run(signed_in, pid, "ILLUSTRATIONS")
    await yield_and_drain()

    failed_calls = fake_gemini.calls[before_failure:]
    assert [call.kind for call in failed_calls] == ["image", "image"]
    assert failed_calls[0].prompt == prompts.CHAPTER_SEED
    assert failed_calls[0].previous_interaction_id == image_head
    assert failed_calls[1].prompt == prompts.ILLUSTRATION_INSTRUCTION.format(
        name=chapter["name"], prompt=chapter["prompt"]
    )
    assert failed_calls[1].previous_interaction_id is not None
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["failure"]["code"] == "GEMINI_ERROR"
    assert [chapter["image_state"] for chapter in project["chapters"]] == [
        "pending"
    ]
    assert raw_heads(settings, pid) == (text_head, None)

    before_retry = len(fake_gemini.calls)
    await run(signed_in, pid, "ILLUSTRATIONS")

    retry_calls = fake_gemini.calls[before_retry:]
    assert [call.kind for call in retry_calls] == ["image"]
    assert retry_calls[0].prompt == prompts.ILLUSTRATION_STANDALONE.format(
        name=chapter["name"], prompt=chapter["prompt"]
    )
    assert retry_calls[0].previous_interaction_id is None
    assert retry_calls[0].reference_image_count == 2
    assert retry_calls[0].system_instruction == prompts.RULES
    assert all(call.kind != "upload" for call in retry_calls)
    retried = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert [chapter["image_state"] for chapter in retried["chapters"]] == [
        "ready"
    ]
    assert retried["status"] == "DONE"
    final_text_head, final_image_head = raw_heads(settings, pid)
    assert final_text_head == text_head
    assert final_image_head is not None


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
    signed_in, settings, fake_gemini
):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE"])
    text_head_before, _ = raw_heads(settings, pid)
    assert text_head_before is not None
    fake_gemini.extra_items = 3

    await run(signed_in, pid, "CHARACTERS")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["failure"]["code"] == "INVALID_OUTPUT"
    assert project["characters"] == []
    assert project["status"] == "STYLE_SET"
    text_head_after, _ = raw_heads(settings, pid)
    assert text_head_after == text_head_before
    with db.get_conn(settings) as conn:
        child_count = conn.execute(
            "SELECT COUNT(*) FROM characters WHERE project_id=?", (pid,)
        ).fetchone()[0]
    assert child_count == 0
