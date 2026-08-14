import pytest

from app import db, files, store
from app.gemini import prompts
from app.gemini.fake import FakeGeminiClient
from app.gemini.protocol import InvalidStructuredOutput
from app.handlers import StepContext, run_step
from app.steps import StepName

BOOK = "Chapter 1. The river bank. Mole had been working very hard all the morning."


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as c:
        db.init_schema(c)
        yield c


@pytest.fixture
def project(conn, settings):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    project_id = store.new_id()
    book_path = files.write_book(settings.data_dir, project_id, BOOK)
    conn.execute(
        "INSERT INTO projects (id,user_id,title,created_at,book_path,book_excerpt,"
        "status,step_state) VALUES (?,?,?,?,?,?, 'CREATED','IDLE')",
        (project_id, user_id, "The Wind in the Willows", store.now_iso(),
         book_path, files.excerpt(BOOK)),
    )
    return user_id, project_id


@pytest.fixture
def ctx(settings, fake_gemini, project):
    user_id, project_id = project
    return StepContext(project_id=project_id, user_id=user_id, settings=settings,
                       gemini=fake_gemini, notify=lambda: None)


def project_row(conn, ctx):
    return store.get_project(conn, ctx.project_id, ctx.user_id)


# --------------------------------------------------------------------------
# Step 1 - Style
# --------------------------------------------------------------------------

async def test_generated_style_uploads_the_book_seeds_then_asks_for_a_style(
        conn, ctx, fake_gemini):
    await run_step(StepName.STYLE, ctx, style=None)

    kinds = [c.kind for c in fake_gemini.calls]
    assert kinds == ["upload", "text", "text"]

    seed = fake_gemini.calls[1]
    assert seed.prompt == prompts.BOOK_INTRO
    assert seed.document_uri is not None          # the book travels with the seed
    assert seed.previous_interaction_id is None

    style_call = fake_gemini.calls[2]
    assert style_call.prompt == prompts.STYLE_GENERATE
    assert style_call.previous_interaction_id is not None   # chained off the book
    assert style_call.document_uri is None                  # never re-sent


async def test_generated_style_is_persisted_raw_with_the_new_text_head(conn, ctx):
    await run_step(StepName.STYLE, ctx, style=None)
    row = project_row(conn, ctx)
    assert row["style_text"] == FakeGeminiClient.STYLE_TEXT
    assert 'Follow this style' not in row["style_text"]   # wrapper applied at use
    assert row["text_interaction_id"] is not None


async def test_a_user_supplied_style_acknowledges_instead_of_generating(
        conn, ctx, fake_gemini):
    await run_step(StepName.STYLE, ctx, style="  bold linocut  ")

    assert fake_gemini.calls[2].prompt == \
        prompts.STYLE_ACKNOWLEDGE.format(style="bold linocut")
    assert project_row(conn, ctx)["style_text"] == "bold linocut"


async def test_both_style_paths_produce_the_same_state_shape(conn, ctx, fake_gemini):
    await run_step(StepName.STYLE, ctx, style="bold linocut")
    row = project_row(conn, ctx)
    assert row["style_text"] and row["text_interaction_id"]
    assert [c.kind for c in fake_gemini.calls] == ["upload", "text", "text"]


async def test_a_blank_style_string_is_treated_as_no_style(conn, ctx, fake_gemini):
    await run_step(StepName.STYLE, ctx, style="   ")
    assert fake_gemini.calls[2].prompt == prompts.STYLE_GENERATE


async def test_a_style_already_persisted_is_not_regenerated(conn, ctx, fake_gemini):
    """Resume-aware: a crash after save_style but before the status advance
    leaves nothing to redo (design 6.2)."""
    store.save_style(conn, ctx.project_id, style_text="already here",
                     text_interaction_id="i-old")

    await run_step(StepName.STYLE, ctx, style=None)

    assert fake_gemini.calls == []
    assert project_row(conn, ctx)["style_text"] == "already here"


# --------------------------------------------------------------------------
# Step 2 - Characters
# --------------------------------------------------------------------------

@pytest.fixture
def styled(conn, ctx):
    store.save_style(conn, ctx.project_id, style_text="Warm watercolour",
                     text_interaction_id="i-style")
    return ctx


async def test_characters_chain_off_the_text_head_and_never_resend_the_book(
        conn, styled, fake_gemini):
    await run_step(StepName.CHARACTERS, styled)

    assert [c.kind for c in fake_gemini.calls] == ["structured"]
    call = fake_gemini.calls[0]
    assert call.prompt == prompts.CHARACTERS_INSTRUCTION
    assert call.previous_interaction_id == "i-style"
    assert call.document_uri is None
    assert call.max_items == 2


async def test_characters_are_persisted_in_order_with_the_new_text_head(conn, styled):
    await run_step(StepName.CHARACTERS, styled)

    rows = store.list_characters(conn, styled.project_id)
    assert [r["name"] for r in rows] == ["Toad", "Ratty"]
    assert [r["position"] for r in rows] == [0, 1]
    assert all(r["portrait_path"] is None for r in rows)
    assert project_row(conn, styled)["text_interaction_id"] != "i-style"


async def test_at_most_two_characters_are_ever_requested(conn, styled, fake_gemini):
    await run_step(StepName.CHARACTERS, styled)
    assert fake_gemini.calls[0].max_items == 2


async def test_an_over_cap_response_fails_validation_rather_than_being_sliced(
        conn, styled, fake_gemini):
    """No silent slicing: three characters is INVALID_OUTPUT with nothing
    persisted (design 7.4)."""
    fake_gemini.extra_items = 3

    with pytest.raises(InvalidStructuredOutput, match="at most 2"):
        await run_step(StepName.CHARACTERS, styled)

    assert store.list_characters(conn, styled.project_id) == []


async def test_a_response_missing_a_prompt_fails_validation(conn, styled, fake_gemini):
    fake_gemini.CHARACTER_ITEMS = [{"name": "Toad"}]

    with pytest.raises(InvalidStructuredOutput, match="usable name and prompt"):
        await run_step(StepName.CHARACTERS, styled)

    assert store.list_characters(conn, styled.project_id) == []


async def test_a_null_text_head_makes_one_standalone_call_that_re_uploads_the_book(
        conn, styled, fake_gemini):
    """The NULL head IS the recovery branch. Step 2's prompt genuinely needs the
    book - 'use the descriptions from the book' - so no artifact substitutes
    (design 7.5)."""
    conn.execute("UPDATE projects SET text_interaction_id = NULL WHERE id = ?",
                 (styled.project_id,))

    await run_step(StepName.CHARACTERS, styled)

    assert [c.kind for c in fake_gemini.calls] == ["upload", "structured"]
    call = fake_gemini.calls[1]
    assert call.previous_interaction_id is None
    assert call.document_uri is not None
    assert "Warm watercolour" in call.prompt
    assert prompts.CHARACTERS_INSTRUCTION in call.prompt


async def test_characters_already_persisted_are_not_regenerated(conn, styled, fake_gemini):
    store.save_characters(conn, styled.project_id, [("Existing", "p")],
                          text_interaction_id="i-old")

    await run_step(StepName.CHARACTERS, styled)

    assert fake_gemini.calls == []
    assert [r["name"] for r in store.list_characters(conn, styled.project_id)] == ["Existing"]


# --------------------------------------------------------------------------
# Step 3 - Portraits
# --------------------------------------------------------------------------

@pytest.fixture
def with_characters(conn, styled):
    store.save_characters(conn, styled.project_id,
                          [("Toad", "a stout toad"), ("Ratty", "a trim rat")],
                          text_interaction_id="i-chars")
    return styled


async def test_portraits_seed_the_image_chain_unchained_then_chain_each_portrait(
        conn, with_characters, fake_gemini):
    """The image chain is seeded fresh. Notebook cell 34 is a bare TODO about
    chaining an image call off a text interaction - Google has not validated it,
    and neither do we (design 7.1)."""
    await run_step(StepName.PORTRAITS, with_characters)

    assert [c.kind for c in fake_gemini.calls] == ["image", "image", "image"]
    seed, first, second = fake_gemini.calls
    assert seed.previous_interaction_id is None
    assert "The Wind in the Willows" in seed.prompt
    assert 'Follow this style: "Warm watercolour"' in seed.prompt
    assert "no text on the image" in seed.prompt

    assert first.prompt == prompts.PORTRAIT_INSTRUCTION.format(
        name="Toad", prompt="a stout toad")
    assert first.previous_interaction_id == "fake-interaction-1"
    assert second.previous_interaction_id == "fake-interaction-2"
    assert project_row(conn, with_characters)["image_interaction_id"] == \
        "fake-interaction-3"


async def test_each_portrait_lands_on_disk_and_advances_the_image_head(
        conn, with_characters, settings):
    await run_step(StepName.PORTRAITS, with_characters)

    rows = store.list_characters(conn, with_characters.project_id)
    for row in rows:
        assert row["portrait_path"] == \
            f"projects/{with_characters.project_id}/portraits/{row['id']}.png"
        assert files.absolute(settings.data_dir, row["portrait_path"]).exists()
    assert project_row(conn, with_characters)["image_interaction_id"] is not None


async def test_the_view_is_notified_after_each_portrait_not_only_at_the_end(
        conn, with_characters, settings, fake_gemini):
    """Per-item progress: the user sees each portrait land (assessment 4.4)."""
    seen: list[int] = []

    def count_ready() -> None:
        with db.get_conn(settings) as c:
            seen.append(sum(1 for r in store.list_characters(c, with_characters.project_id)
                            if r["portrait_path"]))

    ctx_with_notify = StepContext(
        project_id=with_characters.project_id, user_id=with_characters.user_id,
        settings=settings, gemini=fake_gemini, notify=count_ready)
    await run_step(StepName.PORTRAITS, ctx_with_notify)

    assert seen == [1, 2]


async def test_an_existing_portrait_is_never_regenerated(conn, with_characters, fake_gemini):
    """Crash after portrait 1, before portrait 2: the retry calls character 1
    zero times and character 2 once (design 6.2)."""
    first = store.list_characters(conn, with_characters.project_id)[0]
    store.save_portrait(conn, project_id=with_characters.project_id,
                        character_id=first["id"],
                        portrait_path="projects/p/portraits/kept.png",
                        image_interaction_id="i-img-1")

    await run_step(StepName.PORTRAITS, with_characters)

    image_prompts = [c.prompt for c in fake_gemini.calls if c.kind == "image"]
    assert not any("a stout toad" in p for p in image_prompts)
    assert any("a trim rat" in p for p in image_prompts)
    assert store.list_characters(conn, with_characters.project_id)[0]["portrait_path"] == \
        "projects/p/portraits/kept.png"


async def test_a_live_image_head_is_reused_rather_than_reseeded(
        conn, with_characters, fake_gemini):
    conn.execute("UPDATE projects SET image_interaction_id='i-img-live' WHERE id=?",
                 (with_characters.project_id,))

    await run_step(StepName.PORTRAITS, with_characters)

    assert fake_gemini.calls[0].previous_interaction_id == "i-img-live"
    assert len(fake_gemini.calls) == 2      # no seed call


async def test_a_reseed_after_expiry_carries_the_portraits_already_on_disk(
        conn, with_characters, settings, fake_gemini):
    """Step 3's standalone seed carries style, rules and any existing portraits
    as references, so a rebuilt chain keeps character consistency (design 7.5)."""
    first = store.list_characters(conn, with_characters.project_id)[0]
    path = files.save_portrait_bytes(settings.data_dir, with_characters.project_id,
                                     first["id"], FakeGeminiClient.TINY_PNG)
    store.save_portrait(conn, project_id=with_characters.project_id,
                        character_id=first["id"], portrait_path=path,
                        image_interaction_id="i-old")
    conn.execute("UPDATE projects SET image_interaction_id = NULL WHERE id = ?",
                 (with_characters.project_id,))

    await run_step(StepName.PORTRAITS, with_characters)

    assert fake_gemini.calls[0].previous_interaction_id is None
    assert fake_gemini.calls[0].reference_image_count == 1


async def test_the_generation_loop_is_bounded_regardless_of_how_many_rows_exist(
        conn, with_characters, fake_gemini):
    """The cost invariant. Seeded directly rather than through Gemini output,
    because this is a different mechanism guarding a different failure
    (design 7.4)."""
    conn.execute(
        "INSERT INTO characters (id, project_id, position, name, prompt) VALUES (?,?,?,?,?)",
        (store.new_id(), with_characters.project_id, 2, "Badger", "a broad badger"))

    await run_step(StepName.PORTRAITS, with_characters)

    portrait_calls = [c for c in fake_gemini.calls
                      if c.kind == "image" and c.previous_interaction_id is not None]
    assert len(portrait_calls) == 2
    assert not any("badger" in c.prompt.lower() for c in portrait_calls)


async def test_nothing_left_to_generate_makes_no_calls_at_all(
        conn, with_characters, fake_gemini):
    for row in store.list_characters(conn, with_characters.project_id):
        store.save_portrait(conn, project_id=with_characters.project_id,
                            character_id=row["id"], portrait_path="projects/p/x.png",
                            image_interaction_id="i")

    await run_step(StepName.PORTRAITS, with_characters)

    assert fake_gemini.calls == []


# --------------------------------------------------------------------------
# Step 4 - Chapters
# --------------------------------------------------------------------------

async def test_chapters_chain_off_the_characters_interaction_not_the_image_chain(
        conn, with_characters, fake_gemini):
    """Step 4 chains off the text head, which after step 2 IS the characters
    interaction - so no history table is needed (design 7.1)."""
    conn.execute("UPDATE projects SET image_interaction_id='i-img-2' WHERE id=?",
                 (with_characters.project_id,))

    await run_step(StepName.CHAPTERS, with_characters)

    assert [c.kind for c in fake_gemini.calls] == ["structured"]
    call = fake_gemini.calls[0]
    assert call.previous_interaction_id == "i-chars"
    assert call.previous_interaction_id != "i-img-2"
    assert call.prompt == prompts.CHAPTERS_INSTRUCTION
    assert call.max_items == 1
    assert call.document_uri is None


async def test_one_chapter_is_persisted_with_the_new_text_head(conn, with_characters):
    await run_step(StepName.CHAPTERS, with_characters)

    rows = store.list_chapters(conn, with_characters.project_id)
    assert [r["name"] for r in rows] == ["Chapter One"]
    assert rows[0]["illustration_path"] is None
    assert project_row(conn, with_characters)["text_interaction_id"] != "i-chars"


async def test_more_than_one_chapter_fails_validation(conn, with_characters, fake_gemini):
    fake_gemini.extra_items = 2

    with pytest.raises(InvalidStructuredOutput, match="at most 1"):
        await run_step(StepName.CHAPTERS, with_characters)

    assert store.list_chapters(conn, with_characters.project_id) == []


async def test_a_null_text_head_rebuilds_from_style_and_the_character_prompts(
        conn, with_characters, fake_gemini):
    conn.execute("UPDATE projects SET text_interaction_id = NULL WHERE id = ?",
                 (with_characters.project_id,))

    await run_step(StepName.CHAPTERS, with_characters)

    assert [c.kind for c in fake_gemini.calls] == ["upload", "structured"]
    call = fake_gemini.calls[1]
    assert call.previous_interaction_id is None
    assert call.document_uri is not None       # 'for each chapters of the book'
    assert "Warm watercolour" in call.prompt
    assert "a stout toad" in call.prompt        # characters carried forward
    assert "a trim rat" in call.prompt


async def test_chapters_already_persisted_are_not_regenerated(
        conn, with_characters, fake_gemini):
    store.save_chapters(conn, with_characters.project_id, [("Existing", "p")],
                        text_interaction_id="i-old")

    await run_step(StepName.CHAPTERS, with_characters)

    assert fake_gemini.calls == []


# --------------------------------------------------------------------------
# Step 5 - Illustrations
# --------------------------------------------------------------------------

@pytest.fixture
def with_chapters(conn, with_characters, settings):
    for row in store.list_characters(conn, with_characters.project_id):
        path = files.save_portrait_bytes(settings.data_dir, with_characters.project_id,
                                         row["id"], FakeGeminiClient.TINY_PNG)
        store.save_portrait(conn, project_id=with_characters.project_id,
                            character_id=row["id"], portrait_path=path,
                            image_interaction_id="i-img-2")
    store.save_chapters(conn, with_characters.project_id,
                        [("Chapter One", "a sunlit river bank")],
                        text_interaction_id="i-chaps")
    return with_characters


async def test_illustrations_seed_chapter_mode_off_the_last_portrait_then_draw(
        conn, with_chapters, fake_gemini):
    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    assert [c.kind for c in fake_gemini.calls] == ["image", "image"]
    seed, draw = fake_gemini.calls
    assert seed.prompt == prompts.CHAPTER_SEED
    assert seed.previous_interaction_id == "i-img-2"     # continues the image chain
    assert draw.prompt == prompts.ILLUSTRATION_INSTRUCTION.format(
        name="Chapter One", prompt="a sunlit river bank")
    assert draw.previous_interaction_id == "fake-interaction-1"
    assert draw.reference_image_count == 0               # chained mode needs no refs
    assert project_row(conn, with_chapters)["image_interaction_id"] == \
        "fake-interaction-2"


async def test_the_illustration_lands_on_disk_and_completes_the_project_data(
        conn, with_chapters, settings):
    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    row = store.list_chapters(conn, with_chapters.project_id)[0]
    assert row["illustration_path"] == \
        f"projects/{with_chapters.project_id}/illustrations/{row['id']}.png"
    assert files.absolute(settings.data_dir, row["illustration_path"]).exists()


async def test_a_null_image_head_draws_standalone_with_the_portraits_as_references(
        conn, with_chapters, fake_gemini):
    """Notebook cells 39-44: reference images plus the rules as
    system_instruction, and no chaining. Every persisted portrait is sent,
    because at a cap of 2 that is the same set cell 44 would select (design 7.5)."""
    conn.execute("UPDATE projects SET image_interaction_id = NULL WHERE id = ?",
                 (with_chapters.project_id,))

    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    assert [c.kind for c in fake_gemini.calls] == ["image"]
    call = fake_gemini.calls[0]
    assert call.previous_interaction_id is None
    assert call.reference_image_count == 2
    assert call.system_instruction == prompts.RULES
    assert "a sunlit river bank" in call.prompt


async def test_the_standalone_illustration_never_re_uploads_the_book(
        conn, with_chapters, fake_gemini):
    conn.execute("UPDATE projects SET image_interaction_id = NULL WHERE id = ?",
                 (with_chapters.project_id,))
    await run_step(StepName.ILLUSTRATIONS, with_chapters)
    assert not any(c.kind == "upload" for c in fake_gemini.calls)


async def test_the_illustration_loop_is_bounded_at_one_chapter(
        conn, with_chapters, fake_gemini):
    conn.execute(
        "INSERT INTO chapters (id, project_id, position, name, prompt) VALUES (?,?,?,?,?)",
        (store.new_id(), with_chapters.project_id, 1, "Chapter Two", "the wild wood"))

    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    drawn = [c for c in fake_gemini.calls if c.prompt and "wild wood" in c.prompt]
    assert drawn == []


async def test_an_existing_illustration_is_not_regenerated(conn, with_chapters, fake_gemini):
    row = store.list_chapters(conn, with_chapters.project_id)[0]
    store.save_illustration(conn, project_id=with_chapters.project_id, chapter_id=row["id"],
                            illustration_path="projects/p/x.png", image_interaction_id="i")

    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    assert fake_gemini.calls == []


async def test_the_image_chain_seed_does_not_ask_for_an_image(conn, with_characters,
                                                              fake_gemini):
    """The seed establishes style and rules; the model replies in prose. Marking
    it expect_image=False is what stops step 3 failing before any portrait is
    attempted (found by UAT against live Gemini, not by the fake)."""
    await run_step(StepName.PORTRAITS, with_characters)

    image_calls = [c for c in fake_gemini.calls if c.kind == "image"]
    seed, *portraits = image_calls
    assert seed.expect_image is False, "the chain seed must not demand an image"
    assert seed.previous_interaction_id is None
    assert portraits, "expected portrait calls after the seed"
    assert all(c.expect_image is True for c in portraits), \
        "portrait calls must still require a real image"


async def test_the_chapter_mode_seed_does_not_ask_for_an_image(conn, with_chapters,
                                                               fake_gemini):
    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    image_calls = [c for c in fake_gemini.calls if c.kind == "image"]
    seed, *drawn = image_calls
    assert seed.prompt == prompts.CHAPTER_SEED
    assert seed.expect_image is False
    assert all(c.expect_image is True for c in drawn)
