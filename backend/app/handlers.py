"""The five step handlers.

Each does only the work not already persisted, which makes a retry cheap and
lossless within a step. They are resume-aware, not idempotent: a Gemini call
whose response was lost to process death leaves nothing on disk, so a later
user-triggered retry genuinely repeats it (design 6.2).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Sequence

from app import db, files, store
from app.config import Settings
from app.gemini import prompts
from app.gemini.protocol import (
    GeminiClient, InvalidStructuredOutput, ReferenceImage, PROMPT_ITEM_SCHEMA,
)
from app.steps import MAX_CHAPTERS, MAX_CHARACTERS, StepName


@dataclass(frozen=True)
class StepContext:
    project_id: str
    user_id: str
    settings: Settings
    gemini: GeminiClient
    notify: Callable[[], None]


def _validated(items: list[dict], cap: int, label: str) -> list[dict]:
    """Strict validation wins. There is no silent slicing anywhere in the parse
    path: an over-cap response is a failure, not a truncated success (design 7.4)."""
    if not items:
        raise InvalidStructuredOutput(f"Gemini returned no {label}.")
    if len(items) > cap:
        raise InvalidStructuredOutput(
            f"Gemini returned {len(items)} {label} but at most {cap} are allowed.")
    for item in items:
        if (not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("prompt"), str)
                or not item["name"].strip() or not item["prompt"].strip()):
            raise InvalidStructuredOutput(
                f"Gemini returned a {label} entry without a usable name and prompt.")
    return items


def _reference_images(ctx: StepContext, rows: Sequence[sqlite3.Row],
                      column: str) -> list[ReferenceImage]:
    refs: list[ReferenceImage] = []
    for row in rows:
        path = row[column]
        if path:
            refs.append(ReferenceImage(
                data=files.absolute(ctx.settings.data_dir, path).read_bytes(),
                mime_type="image/png"))
    return refs


def _load(ctx: StepContext) -> tuple[sqlite3.Row, list[sqlite3.Row], list[sqlite3.Row]]:
    with db.get_conn(ctx.settings) as conn:
        return (store.get_project(conn, ctx.project_id, ctx.user_id),
                store.list_characters(conn, ctx.project_id),
                store.list_chapters(conn, ctx.project_id))


# --------------------------------------------------------------------------
# Step 1 - Style
# --------------------------------------------------------------------------

async def run_style(ctx: StepContext, *, style: str | None) -> None:
    row, _, _ = _load(ctx)
    if row["style_text"] is not None:
        return  # already persisted; nothing left to do in this step

    book_uri = await ctx.gemini.upload_book(
        files.book_path(ctx.settings.data_dir, ctx.project_id))
    seed = await ctx.gemini.create_text(prompt=prompts.BOOK_INTRO, document_uri=book_uri)

    supplied = (style or "").strip()
    if supplied:
        result = await ctx.gemini.create_text(
            prompt=prompts.STYLE_ACKNOWLEDGE.format(style=supplied),
            previous_interaction_id=seed.interaction_id)
        style_text = supplied
    else:
        result = await ctx.gemini.create_text(
            prompt=prompts.STYLE_GENERATE,
            previous_interaction_id=seed.interaction_id)
        style_text = result.text

    # Stored raw. The notebook's 'Follow this style: "…"' wrapper is applied when
    # building an image prompt - formatting belongs at the point of use (design 7.2).
    with db.get_conn(ctx.settings) as conn:
        store.save_style(conn, ctx.project_id, style_text=style_text,
                         text_interaction_id=result.interaction_id)


async def run_step(step: StepName, ctx: StepContext, *, style: str | None = None) -> None:
    if step == StepName.STYLE:
        await run_style(ctx, style=style)
    else:
        raise NotImplementedError(step)   # Tasks 19-22 fill this in
