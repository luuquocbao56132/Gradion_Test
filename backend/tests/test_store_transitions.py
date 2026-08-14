import pytest

from app import db, store
from app.steps import ProjectStatus, StepState


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as c:
        db.init_schema(c)
        yield c


@pytest.fixture
def pid(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    return store.create_project(conn, project_id=store.new_id(), user_id=user_id,
                                title="W", book_path="p", book_excerpt="e")


def row(conn, pid):
    return conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()


def begin(conn, pid, *, expected=ProjectStatus.CREATED, run="run-A"):
    return store.begin_step(conn, pid, expected_status=expected,
                            server_run_id=run, now="2026-08-14T10:00:00+00:00")


def test_beginning_the_current_step_claims_the_attempt(conn, pid):
    assert begin(conn, pid) is True
    r = row(conn, pid)
    assert r["step_state"] == StepState.RUNNING
    assert r["server_run_id"] == "run-A"
    assert r["step_started_at"] == "2026-08-14T10:00:00+00:00"


def test_beginning_a_future_step_is_refused(conn, pid):
    """Step ordering: assessment 4.3 - a step cannot run before its predecessors."""
    assert begin(conn, pid, expected=ProjectStatus.CHARACTERS_GENERATED) is False
    assert row(conn, pid)["step_state"] == StepState.IDLE


def test_beginning_an_already_completed_step_is_refused(conn, pid):
    conn.execute("UPDATE projects SET status='STYLE_SET' WHERE id=?", (pid,))
    assert begin(conn, pid, expected=ProjectStatus.CREATED) is False


def test_a_second_caller_cannot_claim_a_live_run(conn, pid):
    """The duplicate-execution guard: refresh, second tab, double-click."""
    assert begin(conn, pid) is True
    assert begin(conn, pid) is False
    assert row(conn, pid)["server_run_id"] == "run-A"


def test_a_run_left_by_a_dead_process_can_be_reclaimed(conn, pid):
    """Orphan recovery is not a separate endpoint - retrying IS the recovery,
    permitted only when the owning process is provably gone (design 5.1)."""
    assert begin(conn, pid, run="run-A") is True
    assert begin(conn, pid, run="run-B") is True
    assert row(conn, pid)["server_run_id"] == "run-B"


def test_a_failed_step_can_be_retried(conn, pid):
    begin(conn, pid)
    store.fail_step(conn, pid, server_run_id="run-A", code="GEMINI_ERROR", message="boom")
    assert row(conn, pid)["step_state"] == StepState.FAILED
    assert begin(conn, pid) is True


def test_beginning_clears_any_previous_error(conn, pid):
    begin(conn, pid)
    store.fail_step(conn, pid, server_run_id="run-A", code="GEMINI_ERROR", message="boom")
    begin(conn, pid)
    r = row(conn, pid)
    assert r["error_code"] is None and r["error_message"] is None


def test_completing_moves_status_and_step_state_in_one_write(conn, pid):
    begin(conn, pid)
    assert store.complete_step(conn, pid, server_run_id="run-A",
                               next_status=ProjectStatus.STYLE_SET) is True
    r = row(conn, pid)
    assert (r["status"], r["step_state"], r["server_run_id"]) == \
        (ProjectStatus.STYLE_SET, StepState.IDLE, None)


def test_completing_is_refused_when_this_run_no_longer_owns_the_step(conn, pid):
    """A task whose run was taken over must not advance someone else's step."""
    begin(conn, pid, run="run-A")
    begin(conn, pid, run="run-B")
    assert store.complete_step(conn, pid, server_run_id="run-A",
                               next_status=ProjectStatus.STYLE_SET) is False
    assert row(conn, pid)["status"] == ProjectStatus.CREATED


def test_failing_is_refused_when_this_run_no_longer_owns_the_step(conn, pid):
    begin(conn, pid, run="run-A")
    begin(conn, pid, run="run-B")
    assert store.fail_step(conn, pid, server_run_id="run-A",
                           code="INTERNAL", message="late") is False
    assert row(conn, pid)["step_state"] == StepState.RUNNING


def test_failing_can_null_the_chain_head_that_raised(conn, pid):
    """Context expiry does two things in one write: fail, and null that chain
    (design 7.5). Nothing else happens in that run."""
    conn.execute("UPDATE projects SET text_interaction_id='i-t', image_interaction_id='i-i' "
                 "WHERE id=?", (pid,))
    begin(conn, pid)
    store.fail_step(conn, pid, server_run_id="run-A", code="GEMINI_ERROR",
                    message="context expired", clear_head="text")
    r = row(conn, pid)
    assert r["text_interaction_id"] is None
    assert r["image_interaction_id"] == "i-i"


def test_failing_can_null_the_image_head_instead(conn, pid):
    conn.execute("UPDATE projects SET text_interaction_id='i-t', image_interaction_id='i-i' "
                 "WHERE id=?", (pid,))
    begin(conn, pid)
    store.fail_step(conn, pid, server_run_id="run-A", code="GEMINI_ERROR",
                    message="context expired", clear_head="image")
    r = row(conn, pid)
    assert r["text_interaction_id"] == "i-t"
    assert r["image_interaction_id"] is None


def test_a_running_row_with_null_run_id_is_reclaimable(conn, pid):
    """Proves IS NOT rather than !=: SQLite's != yields NULL (falsy) when either
    side is NULL, so a RUNNING row with a NULL server_run_id would otherwise be
    permanently stuck - never reclaimable, exactly the failure mode this guards
    against."""
    conn.execute(
        "UPDATE projects SET step_state='RUNNING', server_run_id=NULL WHERE id=?",
        (pid,),
    )
    assert begin(conn, pid, run="run-B") is True
    r = row(conn, pid)
    assert r["step_state"] == StepState.RUNNING
    assert r["server_run_id"] == "run-B"
