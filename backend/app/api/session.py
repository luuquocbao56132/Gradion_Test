from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, Response, status

from app import store
from app.api.deps import SESSION_COOKIE, current_user, get_db
from app.models import SessionCreate, SessionView

router = APIRouter(prefix="/api/session", tags=["session"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionView)
async def sign_in(payload: SessionCreate, response: Response,
                   conn: sqlite3.Connection = Depends(get_db)) -> SessionView:
    user_id = store.upsert_user(conn, email=payload.email, name=payload.name)
    token = store.create_session(conn, user_id)
    # Local-only app, same-origin through the Vite proxy: HttpOnly + Lax is
    # sufficient for this threat model and needs no CORS (design 8.1).
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", path="/")
    return SessionView(user_id=user_id, name=payload.name, email=payload.email)


@router.get("", response_model=SessionView)
async def read_session(user: sqlite3.Row = Depends(current_user)) -> SessionView:
    return SessionView(user_id=user["id"], name=user["name"], email=user["email"])


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(request: Request, response: Response,
                    conn: sqlite3.Connection = Depends(get_db)) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store.delete_session(conn, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
