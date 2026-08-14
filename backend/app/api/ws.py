from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app import db, store
from app.api.deps import SESSION_COOKIE
from app.models import state_message
from app.realtime import Subscriber

router = APIRouter()


@router.websocket("/ws/projects/{project_id}")
async def project_socket(websocket: WebSocket, project_id: str) -> None:
    """Live project state. Identity comes from the existing HttpOnly session
    cookie, which the browser sends on a same-origin upgrade: no query-string
    token, no second authentication mechanism (design 9.2)."""
    settings = websocket.app.state.settings
    registry = websocket.app.state.registry

    token = websocket.cookies.get(SESSION_COOKIE)
    with db.get_conn(settings) as conn:
        user = store.user_for_session(conn, token) if token else None
        owned = user is not None and store.get_project(conn, project_id, user["id"]) is not None

    # A close code can only be delivered on an accepted socket, so we accept and
    # close immediately. Nothing is registered and no state is ever sent. The
    # code is the same whether the project is missing or belongs to someone
    # else, matching REST's policy of not confirming existence.
    await websocket.accept()
    if not owned or registry is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    subscriber = Subscriber(websocket)
    writer = asyncio.create_task(subscriber.run())
    try:
        # ---- critical section (R2): register -> read -> offer, no await ----
        registry.register(project_id, subscriber)
        with db.get_conn(settings) as conn:
            view = store.read_project_view(conn, project_id, user["id"],
                                           server_run_id=settings.server_run_id)
        if view is not None:
            subscriber.offer(state_message(view))
        # ---- end critical section ----

        while True:
            # The client sends nothing meaningful; this detects the close.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        registry.unregister(project_id, subscriber)
        subscriber.close()
        writer.cancel()
