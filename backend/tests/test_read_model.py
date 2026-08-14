import pytest

from app import db, store
from app.steps import ProjectStatus, StepName, StepState


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as c:
        db.init_schema(c)
        yield c


@pytest.fixture
def project(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    pid = store.create_project(conn, project_id=store.new_id(), user_id=user_id,
                               title="Willows", book_path="projects/x/book.txt",
                               book_excerpt="Once…")
    return user_id, pid


def view(conn, settings, project):
    user_id, pid = project
    return store.read_project_view(conn, pid, user_id, server_run_id=settings.server_run_id)


def test_a_fresh_project_reads_as_a_draft_awaiting_style(conn, settings, project):
    v = view(conn, settings, project)
    assert v.status == ProjectStatus.CREATED
    assert v.current_step == StepName.STYLE
    assert v.display_status == "Draft"
    assert v.completed_steps == 0
    assert v.style_text is None and v.failure is None
    assert v.characters == [] and v.chapters == []


def test_another_users_id_reads_as_nothing(conn, settings, project):
    _, pid = project
    other = store.upsert_user(conn, email="bob@example.com", name="Bob")
    assert store.read_project_view(conn, pid, other,
                                   server_run_id=settings.server_run_id) is None


def test_style_and_characters_appear_once_saved(conn, settings, project):
    _, pid = project
    store.save_style(conn, pid, style_text="Warm watercolour", text_interaction_id="i-style")
    store.save_characters(conn, pid, [("Toad", "A stout toad…"), ("Rat", "A river rat…")],
                          text_interaction_id="i-chars")
    v = view(conn, settings, project)
    assert v.style_text == "Warm watercolour"
    assert [c.name for c in v.characters] == ["Toad", "Rat"]
    assert [c.position for c in v.characters] == [0, 1]
    assert all(c.image_url is None for c in v.characters)


def test_while_idle_every_missing_portrait_is_merely_pending(conn, settings, project):
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1"), ("Rat", "p2")],
                          text_interaction_id="i")
    assert [c.image_state for c in view(conn, settings, project).characters] == \
        ["pending", "pending"]


def test_while_running_the_first_missing_portrait_is_the_one_generating(conn, settings, project):
    """[null, null] -> generating, pending (design 4.5)."""
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1"), ("Rat", "p2")], text_interaction_id="i")
    conn.execute(
        "UPDATE projects SET status='CHARACTERS_GENERATED', step_state='RUNNING', "
        "server_run_id=? WHERE id=?", (settings.server_run_id, pid))
    assert [c.image_state for c in view(conn, settings, project).characters] == \
        ["generating", "pending"]


def test_a_landed_portrait_is_ready_and_the_next_becomes_generating(conn, settings, project):
    """[path, null] -> ready, generating (design 4.5)."""
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1"), ("Rat", "p2")], text_interaction_id="i")
    first = store.list_characters(conn, pid)[0]["id"]
    store.save_portrait(conn, project_id=pid, character_id=first,
                        portrait_path=f"projects/{pid}/portraits/{first}.png",
                        image_interaction_id="i-img")
    conn.execute(
        "UPDATE projects SET status='CHARACTERS_GENERATED', step_state='RUNNING', "
        "server_run_id=? WHERE id=?", (settings.server_run_id, pid))
    v = view(conn, settings, project)
    assert [c.image_state for c in v.characters] == ["ready", "generating"]
    assert v.characters[0].image_url == f"/api/projects/{pid}/characters/{first}/portrait"
    assert v.characters[1].image_url is None


def test_a_different_running_step_leaves_portraits_merely_pending(conn, settings, project):
    """Only the step that owns the artifact marks one as generating."""
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1"), ("Rat", "p2")], text_interaction_id="i")
    conn.execute(
        "UPDATE projects SET status='STYLE_SET', step_state='RUNNING', server_run_id=? "
        "WHERE id=?", (settings.server_run_id, pid))
    assert [c.image_state for c in view(conn, settings, project).characters] == \
        ["pending", "pending"]


def test_a_recorded_failure_surfaces_as_the_failure_field(conn, settings, project):
    _, pid = project
    conn.execute(
        "UPDATE projects SET step_state='FAILED', error_code='GEMINI_ERROR', "
        "error_message='Gemini said no' WHERE id=?", (pid,))
    v = view(conn, settings, project)
    assert v.failure.code == "GEMINI_ERROR"
    assert v.failure.message == "Gemini said no"
    assert v.needs_attention is True
    assert v.display_status == "In progress"


def test_chapters_derive_identically_to_characters(conn, settings, project):
    _, pid = project
    store.save_chapters(conn, pid, [("Opening Scene", "A river bank…")],
                        text_interaction_id="i")
    conn.execute(
        "UPDATE projects SET status='CHAPTERS_GENERATED', step_state='RUNNING', "
        "server_run_id=? WHERE id=?", (settings.server_run_id, pid))
    assert [c.image_state for c in view(conn, settings, project).chapters] == ["generating"]


def test_saving_characters_replaces_any_previous_set(conn, settings, project):
    _, pid = project
    store.save_characters(conn, pid, [("A", "x"), ("B", "y")], text_interaction_id="i1")
    store.save_characters(conn, pid, [("C", "z")], text_interaction_id="i2")
    assert [c.name for c in view(conn, settings, project).characters] == ["C"]
    assert conn.execute("SELECT text_interaction_id FROM projects WHERE id=?",
                        (pid,)).fetchone()[0] == "i2"


def test_saving_a_portrait_advances_the_image_head_in_the_same_write(conn, settings, project):
    """Coupling them is what makes step 3 resumable mid-flight (design 7.2)."""
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1")], text_interaction_id="i")
    cid = store.list_characters(conn, pid)[0]["id"]
    store.save_portrait(conn, project_id=pid, character_id=cid,
                        portrait_path="projects/p/portraits/c.png",
                        image_interaction_id="i-img-1")
    row = conn.execute("SELECT image_interaction_id FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["image_interaction_id"] == "i-img-1"
    assert store.list_characters(conn, pid)[0]["portrait_path"] == "projects/p/portraits/c.png"
