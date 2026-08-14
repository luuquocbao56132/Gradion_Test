"""The real Gemini client.

The only module that imports google.genai. Shapes match the notebook and the
findings recorded in docs/gemini-contract.md.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Sequence

from google import genai
from google.genai import types

from app.config import Settings
from app.gemini.protocol import (
    GeminiError, ImageResult, InteractionNotFound, ReferenceImage, StructuredResult,
    TextResult, parse_items,
)


class RealGeminiClient:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or genai.Client(
            api_key=settings.gemini_api_key,
            http_options=self.http_options(settings),
        )

    @staticmethod
    def http_options(settings: Settings) -> types.HttpOptions:
        """`attempts` counts the original request, so 1 means call once and never
        repeat - the SDK compiles it to tenacity.stop_after_attempt(1), its own
        "never retry" strategy. Setting it explicitly matters because we need an
        HttpRetryOptions for the timeout anyway, and `attempts` defaults to 5
        inside one. Assessment 4.3 forbids auto-retry (design 2, 7.7)."""
        return types.HttpOptions(
            timeout=int(settings.request_timeout_seconds * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        )

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _text_of(interaction: Any) -> str:
        if getattr(interaction, "output_text", None):
            return interaction.output_text
        for step in reversed(getattr(interaction, "steps", []) or []):
            if getattr(step, "type", None) == "model_output" and step.content:
                for content in reversed(step.content):
                    if getattr(content, "type", None) == "text" and content.text:
                        return content.text
        raise GeminiError("Gemini returned no text output.")

    @staticmethod
    def _image_of(interaction: Any) -> tuple[bytes, str]:
        candidate = getattr(interaction, "output_image", None)
        if candidate is None:
            for step in reversed(getattr(interaction, "steps", []) or []):
                if getattr(step, "type", None) == "model_output" and step.content:
                    for content in reversed(step.content):
                        if getattr(content, "type", None) == "image":
                            candidate = content
                            break
                    if candidate is not None:
                        break
        if candidate is None:
            raise GeminiError("Gemini returned no image for this prompt.")
        return base64.b64decode(candidate.data), candidate.mime_type

    @staticmethod
    def _translate(exc: Exception, *, had_previous_interaction: bool) -> GeminiError:
        """Q9 in docs/gemini-contract.md: the provider does not distinguish an
        expired interaction from a malformed request - both raise a 400 with
        body code invalid_request, and `.code` does not exist on the exception
        hierarchy interactions.create actually raises. The discriminator has to
        be ours: only a request that carried a previous_interaction_id can mean
        an expired chain head. 404 was never observed but the SDK defines
        NotFoundError for it, so the branch is kept (ruling R17). The asymmetry
        is deliberate - a false positive costs one extra book upload on retry; a
        false negative strands the user resending a dead head forever."""
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "body", None)
        code = body.get("error", {}).get("code") if isinstance(body, dict) else None
        if had_previous_interaction and (
                status == 404 or (status == 400 and code == "invalid_request")):
            return InteractionNotFound(
                "The Gemini conversation for this project no longer exists.")
        return GeminiError(f"Gemini could not complete this request: {exc}")

    async def _create(self, *, had_previous_interaction: bool, **kwargs: Any) -> Any:
        # Catch broadly and discriminate by duck-typing on status_code/body: the
        # real errors are google.genai._gaos.lib.compat_errors classes, which do
        # NOT subclass the public google.genai.errors.APIError, and importing
        # the private _gaos path here would couple production to an SDK
        # internal. Our own GeminiError subclasses pass through untouched, and
        # anything unrecognised still fails the step loudly as GEMINI_ERROR.
        try:
            return await self._client.aio.interactions.create(**kwargs)
        except GeminiError:
            raise
        except Exception as exc:
            raise self._translate(
                exc, had_previous_interaction=had_previous_interaction) from exc

    # ---- GeminiClient -----------------------------------------------------

    async def upload_book(self, book_path: Path) -> str:
        # Q5: pass file.uri (the full https form) onward - the short file.name
        # form is rejected with a 400.
        try:
            return self._client.files.upload(file=str(book_path)).uri
        except Exception as exc:
            raise GeminiError(f"The book could not be uploaded to Gemini: {exc}") from exc

    async def create_text(self, *, prompt: str, previous_interaction_id: str | None = None,
                          document_uri: str | None = None) -> TextResult:
        payload: Any = prompt
        if document_uri is not None:
            payload = [{"type": "text", "text": prompt},
                       {"type": "document", "uri": document_uri}]
        kwargs: dict[str, Any] = {"model": self._settings.text_model, "input": payload}
        if previous_interaction_id is not None:
            kwargs["previous_interaction_id"] = previous_interaction_id
        interaction = await self._create(
            had_previous_interaction=previous_interaction_id is not None, **kwargs)
        return TextResult(interaction_id=interaction.id, text=self._text_of(interaction))

    async def create_structured(self, *, prompt: str,
                                previous_interaction_id: str | None = None,
                                document_uri: str | None = None, item_schema: dict,
                                max_items: int) -> StructuredResult:
        payload: Any = prompt
        if document_uri is not None:
            payload = [{"type": "text", "text": prompt},
                       {"type": "document", "uri": document_uri}]
        kwargs: dict[str, Any] = {
            "model": self._settings.text_model,
            "input": payload,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": {"type": "array", "maxItems": max_items, "items": item_schema},
            },
        }
        if previous_interaction_id is not None:
            kwargs["previous_interaction_id"] = previous_interaction_id
        interaction = await self._create(
            had_previous_interaction=previous_interaction_id is not None, **kwargs)
        return StructuredResult(interaction_id=interaction.id,
                                items=parse_items(self._text_of(interaction)))

    async def create_image(self, *, prompt: str, previous_interaction_id: str | None = None,
                           reference_images: Sequence[ReferenceImage] = (),
                           system_instruction: str | None = None) -> ImageResult:
        payload: Any = prompt
        if reference_images:
            payload = [{"type": "text", "text": prompt}] + [
                {"type": "image",
                 "data": base64.b64encode(ref.data).decode(),
                 "mime_type": ref.mime_type}
                for ref in reference_images
            ]
        kwargs: dict[str, Any] = {"model": self._settings.image_model, "input": payload}
        if previous_interaction_id is not None:
            kwargs["previous_interaction_id"] = previous_interaction_id
        if system_instruction is not None:
            kwargs["system_instruction"] = system_instruction
        interaction = await self._create(
            had_previous_interaction=previous_interaction_id is not None, **kwargs)
        data, mime_type = self._image_of(interaction)
        return ImageResult(interaction_id=interaction.id, data=data, mime_type=mime_type)
