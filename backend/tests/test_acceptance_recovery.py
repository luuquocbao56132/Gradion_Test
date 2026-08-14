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


def seed_raw_heads(settings, pid, *, text_head, image_head):
    with db.get_conn(settings) as conn:
        conn.execute(
            "UPDATE projects SET text_interaction_id=?, image_interaction_id=? "
            "WHERE id=?",
            (text_head, image_head, pid),
        )
    assert raw_heads(settings, pid) == (text_head, image_head)


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


@pytest.mark.parametrize(
    ("step", "prerequisites", "text_head", "image_head", "expected_status"),
    [
        (
            "CHARACTERS",
            ["STYLE"],
            "text-step-2-live",
            "image-step-2-untouched",
            "CHARACTERS_GENERATED",
        ),
        (
            "CHAPTERS",
            ["STYLE", "CHARACTERS", "PORTRAITS"],
            "text-step-4-live",
            "image-step-4-untouched",
            "CHAPTERS_GENERATED",
        ),
    ],
    ids=["step-2-characters", "step-4-chapters"],
)
async def test_text_expiry_clears_only_text_and_user_retry_rebuilds_with_book(
    signed_in,
    settings,
    fake_gemini,
    step,
    prerequisites,
    text_head,
    image_head,
    expected_status,
):
    pid = await create(signed_in)
    await advance(signed_in, pid, prerequisites)
    seed_raw_heads(
        settings,
        pid,
        text_head=text_head,
        image_head=image_head,
    )
    before_failure = len(fake_gemini.calls)
    fake_gemini.fail_on(
        before_failure, InteractionNotFound("interaction expired")
    )

    await run(signed_in, pid, step)
    await yield_and_drain()

    failed_calls = fake_gemini.calls[before_failure:]
    assert len(failed_calls) == 1
    assert [call.kind for call in failed_calls] == ["structured"]
    assert failed_calls[0].previous_interaction_id == text_head
    assert failed_calls[0].document_uri is None
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["failure"]["code"] == "GEMINI_ERROR"
    assert "expired" in project["failure"]["message"]
    assert raw_heads(settings, pid) == (None, image_head)

    before_retry = len(fake_gemini.calls)
    await run(signed_in, pid, step)

    retry_calls = fake_gemini.calls[before_retry:]
    assert [call.kind for call in retry_calls] == ["upload", "structured"]
    assert retry_calls[1].previous_interaction_id is None
    assert retry_calls[1].document_uri == "files/fake-book.txt"
    assert (await signed_in.get(f"/api/projects/{pid}")).json()[
        "status"
    ] == expected_status


@pytest.mark.parametrize(
    (
        "step",
        "prerequisites",
        "text_head",
        "image_head",
        "retry_kinds",
        "expected_status",
    ),
    [
        (
            "PORTRAITS",
            ["STYLE", "CHARACTERS"],
            "text-step-3-untouched",
            "image-step-3-live",
            ["image", "image", "image"],
            "PORTRAITS_GENERATED",
        ),
        (
            "ILLUSTRATIONS",
            ["STYLE", "CHARACTERS", "PORTRAITS", "CHAPTERS"],
            "text-step-5-untouched",
            "image-step-5-live",
            ["image"],
            "DONE",
        ),
    ],
    ids=["step-3-portraits", "step-5-illustrations"],
)
async def test_image_expiry_clears_only_image_and_user_retry_never_uploads_book(
    signed_in,
    settings,
    fake_gemini,
    step,
    prerequisites,
    text_head,
    image_head,
    retry_kinds,
    expected_status,
):
    pid = await create(signed_in)
    await advance(signed_in, pid, prerequisites)
    seed_raw_heads(
        settings,
        pid,
        text_head=text_head,
        image_head=image_head,
    )
    before_failure = len(fake_gemini.calls)
    fake_gemini.fail_on(
        before_failure, InteractionNotFound("interaction expired")
    )

    await run(signed_in, pid, step)
    await yield_and_drain()

    failed_calls = fake_gemini.calls[before_failure:]
    assert len(failed_calls) == 1
    assert [call.kind for call in failed_calls] == ["image"]
    assert failed_calls[0].previous_interaction_id == image_head
    assert raw_heads(settings, pid) == (text_head, None)
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["failure"]["code"] == "GEMINI_ERROR"

    before_retry = len(fake_gemini.calls)
    await run(signed_in, pid, step)

    retry_calls = fake_gemini.calls[before_retry:]
    assert [call.kind for call in retry_calls] == retry_kinds
    assert all(call.kind != "upload" for call in retry_calls)
    assert (await signed_in.get(f"/api/projects/{pid}")).json()[
        "status"
    ] == expected_status


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
