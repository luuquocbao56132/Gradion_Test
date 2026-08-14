from __future__ import annotations

import os
import re
from pathlib import Path

EXCERPT_CHARS = 220
_WHITESPACE = re.compile(r"\s+")


def project_dir(data_dir: Path, project_id: str) -> Path:
    return data_dir / "projects" / project_id


def book_path(data_dir: Path, project_id: str) -> Path:
    return project_dir(data_dir, project_id) / "book.txt"


def _write_atomic(target: Path, payload: bytes) -> None:
    """Write beside the target, then replace it in one operation.

    A crash before the replace leaves only a .tmp file; a crash after it leaves
    a complete file. Because artifact names derive from row ids rather than
    randomness, a retry overwrites its own orphan (design 3.3).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, target)


def write_book(data_dir: Path, project_id: str, text: str) -> str:
    target = book_path(data_dir, project_id)
    _write_atomic(target, text.encode("utf-8"))
    return target.relative_to(data_dir).as_posix()


def read_book(data_dir: Path, project_id: str) -> str:
    return book_path(data_dir, project_id).read_text(encoding="utf-8")


def save_portrait_bytes(data_dir: Path, project_id: str, character_id: str, payload: bytes) -> str:
    target = project_dir(data_dir, project_id) / "portraits" / f"{character_id}.png"
    _write_atomic(target, payload)
    return target.relative_to(data_dir).as_posix()


def save_illustration_bytes(data_dir: Path, project_id: str, chapter_id: str, payload: bytes) -> str:
    target = project_dir(data_dir, project_id) / "illustrations" / f"{chapter_id}.png"
    _write_atomic(target, payload)
    return target.relative_to(data_dir).as_posix()


def absolute(data_dir: Path, relative_path: str) -> Path:
    return (data_dir / relative_path).resolve()


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    collapsed = _WHITESPACE.sub(" ", text).strip()
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"
