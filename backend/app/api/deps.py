from __future__ import annotations

import sqlite3
from typing import AsyncIterator

from fastapi import Depends, HTTPException, Request, status

from app import db, store

SESSION_COOKIE = "session"


async def get_settings(request: Request):
    return request.app.state.settings


async def get_deps(request: Request):
    return request.app.state.deps


async def get_db(request: Request) -> AsyncIterator[sqlite3.Connection]:
    with db.get_conn(request.app.state.settings) as conn:
        yield conn


async def current_user(
    request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> sqlite3.Row:
    token = request.cookies.get(SESSION_COOKIE)
    user = store.user_for_session(conn, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    return user
