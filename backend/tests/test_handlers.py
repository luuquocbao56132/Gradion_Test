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
    fake_gemini.invalid_json_on(0)
    with pytest.raises(InvalidStructuredOutput):
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
