import pytest

from app import pipeline
from app.gemini import prompts

BOOK = "Chapter 1. The river bank. Mole had been working very hard all the morning."
STEPS_IN_ORDER = ["STYLE", "CHARACTERS", "PORTRAITS", "CHAPTERS", "ILLUSTRATIONS"]


class RecordingRegistry:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    def publish(self, project_id: str, payload: dict) -> None:
        self.messages.append((project_id, payload))


@pytest.fixture
async def signed_in(aclient):
    await aclient.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return aclient


async def create(client, title="Willows"):
    return (await client.post(
        "/api/projects", json={"title": title, "book_text": BOOK}
    )).json()["id"]


async def run(client, pid, step, style=None):
    body = {"step": step} if style is None else {"step": step, "style": style}
    response = await client.post(f"/api/projects/{pid}/run", json=body)
    await pipeline.drain_tasks()
    return response


async def test_five_user_actions_take_a_project_to_done(signed_in):
    pid = await create(signed_in)
    expected_counts = [(0, 0), (2, 0), (2, 0), (2, 1), (2, 1)]

    for step, counts in zip(STEPS_IN_ORDER, expected_counts, strict=True):
        assert (await run(signed_in, pid, step)).status_code == 202
        project = (await signed_in.get(f"/api/projects/{pid}")).json()
        assert (len(project["characters"]), len(project["chapters"])) == counts

    assert project["status"] == "DONE"
    assert project["step_state"] == "IDLE"
    assert project["current_step"] is None
    assert project["display_status"] == "Done"
    assert project["completed_steps"] == 5
    assert project["needs_attention"] is False
    assert all(c["image_state"] == "ready" for c in project["characters"])
    assert all(c["image_state"] == "ready" for c in project["chapters"])


async def test_completing_a_step_never_starts_the_next_one(signed_in, fake_gemini):
    """Each step needs its own explicit user action (assessment 4.3)."""
    pid = await create(signed_in)
    for step in STEPS_IN_ORDER:
        await run(signed_in, pid, step)
        calls_after_step = len(fake_gemini.calls)
        await pipeline.drain_tasks()
        assert len(fake_gemini.calls) == calls_after_step


async def test_the_call_and_context_sequence_matches_the_notebook(
        signed_in, fake_gemini):
    pid = await create(signed_in)
    for step in STEPS_IN_ORDER:
        await run(signed_in, pid, step)

    calls = fake_gemini.calls
    assert [c.kind for c in calls] == [
        "upload",       # 0  step 1: the book, once
        "text",         # 1  step 1: book intro + document
        "text",         # 2  step 1: style, chained off the book
        "structured",   # 3  step 2: characters, chained off style
        "image",        # 4  step 3: image seed, UNCHAINED
        "image",        # 5  step 3: portrait 1
        "image",        # 6  step 3: portrait 2, chained off portrait 1
        "structured",   # 7  step 4: chapters, chained off the characters interaction
        "image",        # 8  step 5: chapter-mode seed, chained off portrait 2
        "image",        # 9  step 5: illustration
    ]

    # The book travels exactly once, with the step-1 seed.
    assert calls[1].document_uri is not None
    assert all(c.document_uri is None for c in calls if c is not calls[1])

    # Text chain: book -> style -> characters -> chapters.
    assert calls[1].previous_interaction_id is None
    assert calls[2].previous_interaction_id == "fake-interaction-1"
    assert calls[3].previous_interaction_id == "fake-interaction-2"
    assert calls[3].item_schema is not None and calls[3].max_items == 2
    assert calls[7].previous_interaction_id == "fake-interaction-3"
    assert calls[7].max_items == 1

    # Image chain: seed -> portrait 1 -> portrait 2 -> chapter seed -> illustration.
    assert calls[4].previous_interaction_id is None       # never crosses from text
    assert calls[5].previous_interaction_id == "fake-interaction-4"
    assert calls[6].previous_interaction_id == "fake-interaction-5"
    assert calls[8].previous_interaction_id == "fake-interaction-6"
    assert calls[8].prompt == prompts.CHAPTER_SEED
    assert calls[9].previous_interaction_id == "fake-interaction-8"


async def test_the_book_is_uploaded_exactly_once_across_the_whole_run(
        signed_in, fake_gemini):
    """Assessment 4.3: send the book in step 1 and reuse it across later steps."""
    pid = await create(signed_in)
    upload_counts = []
    for step in STEPS_IN_ORDER:
        await run(signed_in, pid, step)
        upload_counts.append(sum(1 for c in fake_gemini.calls if c.kind == "upload"))

    assert upload_counts == [1, 1, 1, 1, 1]


async def test_the_prompts_sent_are_the_notebooks(signed_in, fake_gemini):
    pid = await create(signed_in)
    for step in STEPS_IN_ORDER:
        await run(signed_in, pid, step)

    calls = fake_gemini.calls
    assert calls[1].prompt == prompts.BOOK_INTRO
    assert calls[2].prompt == prompts.STYLE_GENERATE
    assert calls[3].prompt == prompts.CHARACTERS_INSTRUCTION
    assert calls[7].prompt == prompts.CHAPTERS_INSTRUCTION
    assert calls[8].prompt == prompts.CHAPTER_SEED
    assert "Willows" in calls[4].prompt              # project title, not hardcoded
    assert "no text on the image" in calls[4].prompt  # the rules travel with the seed


async def test_a_user_supplied_style_takes_the_acknowledge_branch(
        signed_in, fake_gemini):
    pid = await create(signed_in)
    await run(signed_in, pid, "STYLE", style="bold linocut, high contrast")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["style_text"] == "bold linocut, high contrast"
    assert fake_gemini.calls[2].prompt == \
        prompts.STYLE_ACKNOWLEDGE.format(style="bold linocut, high contrast")


async def test_both_style_paths_reach_the_same_state_shape(signed_in, fake_gemini):
    generated = await create(signed_in, "Generated")
    await run(signed_in, generated, "STYLE")
    supplied = await create(signed_in, "Supplied")
    await run(signed_in, supplied, "STYLE", style="bold linocut")

    a = (await signed_in.get(f"/api/projects/{generated}")).json()
    b = (await signed_in.get(f"/api/projects/{supplied}")).json()
    assert a["status"] == b["status"] == "STYLE_SET"
    assert a["current_step"] == b["current_step"] == "CHARACTERS"
    assert a["style_text"] != b["style_text"]


async def test_portraits_appear_one_at_a_time_rather_than_all_at_once(
        signed_in, fake_gemini, app):
    """Per-item progress is visible through both REST and pipeline broadcasts."""
    pid = await create(signed_in)
    for step in ["STYLE", "CHARACTERS"]:
        await run(signed_in, pid, step)

    registry = RecordingRegistry()
    app.state.registry = registry
    app.state.deps = pipeline.Deps(
        settings=app.state.settings, gemini=app.state.gemini, registry=registry
    )

    baseline = len(fake_gemini.calls)
    fake_gemini.hold_from(baseline + 2)  # seed and portrait 1 pass
    try:
        await signed_in.post(f"/api/projects/{pid}/run", json={"step": "PORTRAITS"})
        await fake_gemini.wait_for_calls(baseline + 3)

        project = (await signed_in.get(f"/api/projects/{pid}")).json()
        states = [c["image_state"] for c in project["characters"]]
        assert states == ["ready", "generating"]
        assert project["characters"][0]["image_url"] is not None
        assert project["characters"][1]["image_url"] is None

        assert registry.messages
        broadcast_pid, payload = registry.messages[-1]
        assert broadcast_pid == pid
        assert payload["type"] == "project.state"
        assert [c["image_state"] for c in payload["project"]["characters"]] == [
            "ready", "generating"
        ]
        assert payload["project"] == project
    finally:
        fake_gemini.release()
        await pipeline.drain_tasks()
