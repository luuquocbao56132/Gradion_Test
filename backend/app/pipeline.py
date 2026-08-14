"""Step execution.

POST /run performs the conditional transition and returns 202 immediately; the
work runs in a detached in-process task. No queue, no worker process, no broker
- the detached task is the smaller option, not the fancier one (design 6.1).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app import db, store
from app.config import Settings
from app.gemini.protocol import (
    GeminiClient, GeminiError, InteractionNotFound, InvalidStructuredOutput,
)
from app.handlers import StepContext, run_step
from app.models import state_message
from app.steps import StepName, chain_of_step, status_after

CANCELLED_MESSAGE = "The step was cancelled before it finished. Retry to run it again."
EXPIRED_MESSAGE = (
    "The conversation context for this project expired on Gemini's side. "
    "Retry this step and it will be rebuilt from your saved work."
)
INTERNAL_MESSAGE = "Something went wrong while running this step. Retry to try again."

# Python garbage-collects a task nobody holds, so the module keeps a reference
# until it finishes (design 6.1).
_TASKS: set[asyncio.Task] = set()


@dataclass(frozen=True)
class Deps:
    settings: Settings
    gemini: GeminiClient
    registry: object | None = None


def broadcast_state(project_id: str, user_id: str, deps: Deps) -> None:
    """Read the committed state and hand it to the broadcaster.

    pipeline.py builds the payload; realtime.py only moves it. Called strictly
    after COMMIT, never inside a transaction (design 3.3, 9.4).

    Never raises. Broadcast happens strictly after COMMIT, so even total
    broadcaster failure leaves durable state correct - a closed browser tab must
    not fail a pipeline step (design 9.4).
    """
    if deps.registry is None:
        return
    try:
        with db.get_conn(deps.settings) as conn:
            view = store.read_project_view(conn, project_id, user_id,
                                           server_run_id=deps.settings.server_run_id)
        if view is not None:
            deps.registry.publish(project_id, state_message(view))
    except Exception:
        pass


def _record_failure(project_id: str, deps: Deps, code: str, message: str,
                    clear_head: str | None = None) -> None:
    with db.get_conn(deps.settings) as conn:
        store.fail_step(conn, project_id, server_run_id=deps.settings.server_run_id,
                        code=code, message=message, clear_head=clear_head)


async def _execute(*, project_id: str, user_id: str, step: StepName,
                   style: str | None, deps: Deps) -> None:
    ctx = StepContext(
        project_id=project_id, user_id=user_id, settings=deps.settings,
        gemini=deps.gemini,
        notify=lambda: broadcast_state(project_id, user_id, deps),
    )
    try:
        await run_step(step, ctx, style=style)
        with db.get_conn(deps.settings) as conn:
            store.complete_step(conn, project_id,
                                server_run_id=deps.settings.server_run_id,
                                next_status=status_after(step))
    except asyncio.CancelledError:
        # The store write is synchronous, so it completes inside the cancelled
        # task; an awaiting cleanup would be cancelled again at its first
        # suspension point. Re-raised so asyncio still sees a cancelled task
        # and shutdown is not stalled (design 6.3).
        _record_failure(project_id, deps, "INTERNAL", CANCELLED_MESSAGE)
        raise
    except InteractionNotFound:
        # Two things, one write: fail, and null the head of the chain that
        # raised. Nothing else happens in this run - no second call, no
        # automatic reconstruction (design 7.5).
        _record_failure(project_id, deps, "GEMINI_ERROR", EXPIRED_MESSAGE,
                        clear_head=chain_of_step(step))
    except InvalidStructuredOutput as exc:
        _record_failure(project_id, deps, "INVALID_OUTPUT", str(exc))
    except GeminiError as exc:
        _record_failure(project_id, deps, "GEMINI_ERROR", str(exc))
    except Exception:
        _record_failure(project_id, deps, "INTERNAL", INTERNAL_MESSAGE)
    finally:
        broadcast_state(project_id, user_id, deps)


def spawn(*, project_id: str, user_id: str, step: StepName, style: str | None,
          deps: Deps) -> asyncio.Task:
    task = asyncio.create_task(_execute(project_id=project_id, user_id=user_id,
                                        step=step, style=style, deps=deps))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def drain_tasks() -> None:
    """Await every in-flight step task. Used by tests; production never calls it."""
    while _TASKS:
        await asyncio.gather(*list(_TASKS), return_exceptions=True)
