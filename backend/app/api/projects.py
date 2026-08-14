from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app import files, store
from app.api.deps import current_user, get_db, get_settings
from app.config import Settings
from app.models import BookView, ProjectCreate, ProjectListItem, ProjectView

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _load_view(conn, project_id: str, user_id: str, settings: Settings) -> ProjectView:
    view = store.read_project_view(conn, project_id, user_id,
                                   server_run_id=settings.server_run_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    return view


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectView)
async def create_project(payload: ProjectCreate, conn: sqlite3.Connection = Depends(get_db),
                         user: sqlite3.Row = Depends(current_user),
                         settings: Settings = Depends(get_settings)) -> ProjectView:
    """A pure local write. No Gemini call happens here: the upload and the book
    interaction are lazy inside step 1, so an unopened project never begins life
    with a dead file URI (design 7.2)."""
    # The id is minted first so the book file lands in its project-scoped
    # directory; the row then points at a file that already exists.
    project_id = store.new_id()
    book_path = files.write_book(settings.data_dir, project_id, payload.book_text)
    store.create_project(conn, project_id=project_id, user_id=user["id"],
                         title=payload.title, book_path=book_path,
                         book_excerpt=files.excerpt(payload.book_text))
    return _load_view(conn, project_id, user["id"], settings)


@router.get("", response_model=list[ProjectListItem])
async def list_projects(conn: sqlite3.Connection = Depends(get_db),
                        user: sqlite3.Row = Depends(current_user),
                        settings: Settings = Depends(get_settings)) -> list[ProjectListItem]:
    return store.list_projects(conn, user["id"], server_run_id=settings.server_run_id)


@router.get("/{project_id}", response_model=ProjectView)
async def read_project(project_id: str, conn: sqlite3.Connection = Depends(get_db),
                       user: sqlite3.Row = Depends(current_user),
                       settings: Settings = Depends(get_settings)) -> ProjectView:
    return _load_view(conn, project_id, user["id"], settings)


@router.get("/{project_id}/book", response_model=BookView)
async def read_book(project_id: str, conn: sqlite3.Connection = Depends(get_db),
                    user: sqlite3.Row = Depends(current_user),
                    settings: Settings = Depends(get_settings)) -> BookView:
    row = store.get_project(conn, project_id, user["id"])
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    return BookView(text=files.read_book(settings.data_dir, project_id))


def _serve_artifact(conn, *, project_id: str, user: sqlite3.Row, settings: Settings,
                    table: str, row_id: str, column: str) -> Response:
    if store.get_project(conn, project_id, user["id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    row = conn.execute(
        f"SELECT {column} AS path FROM {table} WHERE id = ? AND project_id = ?",
        (row_id, project_id),
    ).fetchone()
    if row is None or row["path"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not generated yet.")
    target = files.absolute(settings.data_dir, row["path"])
    if not target.is_relative_to(settings.data_dir.resolve()) or not target.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return Response(content=target.read_bytes(), media_type="image/png")


@router.get("/{project_id}/characters/{character_id}/portrait")
async def read_portrait(project_id: str, character_id: str,
                        conn: sqlite3.Connection = Depends(get_db),
                        user: sqlite3.Row = Depends(current_user),
                        settings: Settings = Depends(get_settings)) -> Response:
    return _serve_artifact(conn, project_id=project_id, user=user, settings=settings,
                           table="characters", row_id=character_id, column="portrait_path")


@router.get("/{project_id}/chapters/{chapter_id}/illustration")
async def read_illustration(project_id: str, chapter_id: str,
                            conn: sqlite3.Connection = Depends(get_db),
                            user: sqlite3.Row = Depends(current_user),
                            settings: Settings = Depends(get_settings)) -> Response:
    return _serve_artifact(conn, project_id=project_id, user=user, settings=settings,
                           table="chapters", row_id=chapter_id, column="illustration_path")
