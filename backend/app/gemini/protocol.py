from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable


class GeminiError(Exception):
    """Any provider-side failure. Surfaces as error_code GEMINI_ERROR."""


class InteractionNotFound(GeminiError):
    """The chain head no longer exists provider-side. Free-tier interaction
    retention is 1 day, shorter than the assessment's own 3-day deadline, so
    this is expected rather than exceptional (design 7.5)."""


class InvalidStructuredOutput(GeminiError):
    """A structured response that does not satisfy the contract. Nothing is
    persisted; the step fails and the user retries (design 7.4)."""


@dataclass(frozen=True)
class TextResult:
    interaction_id: str
    text: str


@dataclass(frozen=True)
class StructuredResult:
    interaction_id: str
    items: list[dict]


@dataclass(frozen=True)
class ImageResult:
    interaction_id: str
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class ReferenceImage:
    data: bytes
    mime_type: str


# The notebook's Prompt model (cell 25): name + prompt.
PROMPT_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "prompt": {"type": "string"}},
    "required": ["name", "prompt"],
}


def parse_items(text: str) -> list[dict]:
    """Decode a structured response. Never slices, never repairs."""
    if not text or not text.strip():
        raise InvalidStructuredOutput("Gemini returned an empty structured response.")
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidStructuredOutput(
            "Gemini returned a structured response that is not valid JSON."
        ) from exc
    if not isinstance(decoded, list):
        raise InvalidStructuredOutput(
            "Gemini returned a structured response that is not a JSON array."
        )
    return decoded


@runtime_checkable
class GeminiClient(Protocol):
    async def upload_book(self, book_path: Path) -> str: ...

    async def create_text(self, *, prompt: str, previous_interaction_id: str | None = None,
                          document_uri: str | None = None) -> TextResult: ...

    async def create_structured(self, *, prompt: str,
                                previous_interaction_id: str | None = None,
                                document_uri: str | None = None, item_schema: dict,
                                max_items: int) -> StructuredResult: ...

    async def create_image(self, *, prompt: str, previous_interaction_id: str | None = None,
                           reference_images: Sequence[ReferenceImage] = (),
                           system_instruction: str | None = None) -> ImageResult: ...
