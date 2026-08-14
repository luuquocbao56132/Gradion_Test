"""A deterministic stand-in for Gemini, selected by USE_FAKE_GEMINI."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from app.gemini.protocol import ImageResult, InvalidStructuredOutput, ReferenceImage, StructuredResult, TextResult

_TINY_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                 "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

@dataclass
class RecordedCall:
    kind: Literal["upload", "text", "structured", "image"]
    prompt: str | None = None
    previous_interaction_id: str | None = None
    document_uri: str | None = None
    item_schema: dict | None = None
    max_items: int | None = None
    reference_image_count: int = 0
    system_instruction: str | None = None
    # False marks a chain-seeding image call, which returns prose rather than a
    # picture. Recorded so tests can assert seeds are seeds.
    expect_image: bool = True

class FakeGeminiClient:
    TINY_PNG = base64.b64decode(_TINY_PNG_B64)
    STYLE_TEXT = ("Warm hand-painted watercolour with soft ink outlines, "
                  "a storybook feel with gently saturated colour.")
    CHARACTER_ITEMS = [
        {"name": "Toad", "prompt": "A stout, richly dressed adult toad in a green motoring coat and goggles, ruddy and self-satisfied."},
        {"name": "Ratty", "prompt": "A trim adult water rat in a blue waistcoat, sleeve rolled, an oar resting on one shoulder."},
        {"name": "Badger", "prompt": "A broad, grey-striped adult badger in a worn dressing gown, holding a lantern."},
    ]
    CHAPTER_ITEMS = [
        {"name": "Chapter One", "prompt": "Toad and Ratty on a sunlit river bank, the boat drawn up on the grass."},
        {"name": "Chapter Two", "prompt": "Badger's hall under the Wild Wood, firelight."},
    ]

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self.extra_items: int | None = None
        self._failures: dict[int, Exception] = {}
        self._invalid_json: set[int] = set()
        self._hold_from: int | None = None
        self._release = asyncio.Event(); self._release.set()
        self._cond = asyncio.Condition()
        self._next_id = 0

    def fail_on(self, index: int, exc: Exception) -> None: self._failures[index] = exc
    def invalid_json_on(self, index: int) -> None: self._invalid_json.add(index)
    def hold_from(self, index: int) -> None:
        self._hold_from = index; self._release.clear()
    def release(self) -> None: self._release.set()

    async def wait_for_calls(self, n: int, timeout: float = 2.0) -> None:
        async def _wait() -> None:
            async with self._cond:
                await self._cond.wait_for(lambda: len(self.calls) >= n)
        await asyncio.wait_for(_wait(), timeout)

    async def _record(self, call: RecordedCall) -> int:
        index = len(self.calls)
        async with self._cond:
            self.calls.append(call); self._cond.notify_all()
        if self._hold_from is not None and index >= self._hold_from:
            await self._release.wait()
        if (failure := self._failures.get(index)) is not None: raise failure
        return index

    def _mint(self) -> str:
        self._next_id += 1; return f"fake-interaction-{self._next_id}"

    async def upload_book(self, book_path: Path) -> str:
        await self._record(RecordedCall(kind="upload", prompt=str(book_path)))
        return f"files/fake-{book_path.name}"

    async def create_text(self, *, prompt: str, previous_interaction_id: str | None = None,
                          document_uri: str | None = None) -> TextResult:
        await self._record(RecordedCall(kind="text", prompt=prompt, previous_interaction_id=previous_interaction_id, document_uri=document_uri))
        return TextResult(interaction_id=self._mint(), text=self.STYLE_TEXT)

    async def create_structured(self, *, prompt: str, previous_interaction_id: str | None = None,
                                document_uri: str | None = None, item_schema: dict,
                                max_items: int) -> StructuredResult:
        index = await self._record(RecordedCall(kind="structured", prompt=prompt, previous_interaction_id=previous_interaction_id, document_uri=document_uri, item_schema=item_schema, max_items=max_items))
        if index in self._invalid_json:
            raise InvalidStructuredOutput("Gemini returned a structured response that is not valid JSON.")
        source = self.CHAPTER_ITEMS if max_items == 1 else self.CHARACTER_ITEMS
        count = self.extra_items if self.extra_items is not None else max_items
        return StructuredResult(interaction_id=self._mint(), items=[dict(i) for i in source[:count]])

    async def create_image(self, *, prompt: str, previous_interaction_id: str | None = None,
                           reference_images: Sequence[ReferenceImage] = (),
                           system_instruction: str | None = None,
                           expect_image: bool = True) -> ImageResult:
        """A seeding call (`expect_image=False`) returns no image bytes.

        That mirrors the live provider: the image model answers a setup
        instruction in prose with output_image None, and only the next chained
        call produces a picture. Returning a PNG here regardless would let the
        fake hide a real production failure, which is exactly what it did before
        UAT caught it.
        """
        await self._record(RecordedCall(
            kind="image", prompt=prompt, previous_interaction_id=previous_interaction_id,
            reference_image_count=len(reference_images),
            system_instruction=system_instruction, expect_image=expect_image))
        if not expect_image:
            return ImageResult(interaction_id=self._mint(), data=b"", mime_type="")
        return ImageResult(interaction_id=self._mint(), data=self.TINY_PNG, mime_type="image/png")
