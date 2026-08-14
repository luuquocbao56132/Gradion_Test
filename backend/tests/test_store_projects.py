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
def user_id(conn):
    return store.upsert_user(conn, email="ada@example.com", name="Ada")


def test_a_new_project_starts_created_and_idle(conn, user_id):
    pid = store.create_project(
        conn, project_id=store.new_id(), user_id=user_id, title="Willows",
        book_path="projects/x/book.txt", book_excerpt="Once upon a time.")
    row = store.get_project(conn, pid, user_id)
    assert row["status"] == ProjectStatus.CREATED
    assert row["step_state"] == StepState.IDLE
    assert row["server_run_id"] is None
    assert row["style_text"] is None
    assert row["book_excerpt"] == "Once upon a time."


def test_a_project_is_invisible_to_another_user(conn, user_id):
    other = store.upsert_user(conn, email="bob@example.com", name="Bob")
    pid = store.create_project(conn, project_id=store.new_id(), user_id=user_id,
                               title="Willows", book_path="p", book_excerpt="e")
    assert store.get_project(conn, pid, other) is None


def test_list_projects_returns_newest_first_and_only_this_users(conn, user_id, settings):
    other = store.upsert_user(conn, email="bob@example.com", name="Bob")
    for owner, title in [(user_id, "First"), (other, "Theirs"), (user_id, "Second")]:
        store.create_project(conn, project_id=store.new_id(), user_id=owner, title=title,
                             book_path="p", book_excerpt="e")

    items = store.list_projects(conn, user_id, server_run_id=settings.server_run_id)

    assert [i.title for i in items] == ["Second", "First"]
    assert all(i.display_status == "Draft" for i in items)
    assert all(i.completed_steps == 0 for i in items)
    assert all(i.current_step == StepName.STYLE for i in items)
    assert all(i.needs_attention is False for i in items)


def test_a_running_row_from_a_dead_process_lists_as_interrupted(conn, user_id, settings):
    pid = store.create_project(conn, project_id=store.new_id(), user_id=user_id, title="W",
                               book_path="p", book_excerpt="e")
    conn.execute(
        "UPDATE projects SET step_state='RUNNING', server_run_id='a-dead-process' WHERE id=?",
        (pid,))

    item = store.list_projects(conn, user_id, server_run_id=settings.server_run_id)[0]

    assert item.is_interrupted is True
    assert item.needs_attention is True
    assert item.display_status == "In progress"   # never a fourth pill value


def test_a_running_row_from_this_process_is_not_interrupted(conn, user_id, settings):
    pid = store.create_project(conn, project_id=store.new_id(), user_id=user_id, title="W",
                               book_path="p", book_excerpt="e")
    conn.execute("UPDATE projects SET step_state='RUNNING', server_run_id=? WHERE id=?",
                 (settings.server_run_id, pid))

    item = store.list_projects(conn, user_id, server_run_id=settings.server_run_id)[0]

    assert item.is_interrupted is False
    assert item.needs_attention is False
