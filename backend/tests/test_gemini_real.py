import base64
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.gemini.protocol import GeminiError, InteractionNotFound, ReferenceImage
from app.gemini.real import RealGeminiClient

PNG = b"\x89PNG\r\n\x1a\nbytes"


class StubInteractions:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class StubClient:
    def __init__(self, response) -> None:
        self.interactions = StubInteractions(response)
        self.aio = SimpleNamespace(interactions=self.interactions)
        self.files = SimpleNamespace(
            upload=lambda file: SimpleNamespace(uri="files/uploaded-123"))


def interaction(*, text=None, image=None, steps=None):
    return SimpleNamespace(id="interactions/abc", output_text=text,
                           output_image=image, steps=steps or [])


def provider_error(class_name: str, status: int, code: str, message: str) -> Exception:
    """Build the exception class interactions.create actually raises.

    The spike confirmed live errors are google.genai._gaos.lib.compat_errors
    classes, NOT the public google.genai.errors hierarchy - a fixture built from
    errors.ClientError would exercise none of the real attribute access and
    would stay green while the recovery path silently never fired
    (docs/gemini-contract.md, Q9 flagged warning)."""
    from google.genai._gaos.lib import compat_errors

    response = httpx.Response(
        status, request=httpx.Request("POST", "https://gemini.test/interactions"))
    cls = getattr(compat_errors, class_name)
    return cls(f"Error code: {status} - {message}", response=response,
               body={"error": {"message": message, "code": code}})


def make(settings, response):
    stub = StubClient(response)
    return RealGeminiClient(settings, client=stub), stub


def _settings_for_client_config():
    from app.config import Settings
    return Settings(gemini_api_key="k", text_model="t", image_model="i",
                    data_dir=Path("."), db_path=Path("./x.db"),
                    use_fake_gemini=False, server_run_id="r",
                    request_timeout_seconds=12.0)


def test_the_client_makes_exactly_one_attempt_per_request():
    """Asserts the attempt count the SDK actually computes, not the value we
    passed in - `attempts` counts the original request, so the field name reads
    like a retry count when it is really a total. Assessment 4.3 forbids
    auto-retry, and one attempt is how the SDK spells that."""
    from google import genai

    client = genai.Client(
        api_key="k",
        http_options=RealGeminiClient.http_options(_settings_for_client_config()),
    )

    assert client._api_client._retry.stop.max_attempt_number == 1


def test_omitting_attempts_would_silently_enable_four_retries():
    """The footgun this configuration exists to avoid. We need an
    HttpRetryOptions for the timeout anyway, and `attempts` defaults to 5 the
    moment that object exists - which is exactly the shape of notebook cell 12."""
    from google import genai
    from google.genai import types

    careless = genai.Client(api_key="k", http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(initial_delay=2.0)))

    assert careless._api_client._retry.stop.max_attempt_number == 5


def test_the_request_timeout_is_passed_in_milliseconds():
    options = RealGeminiClient.http_options(_settings_for_client_config())
    assert options.timeout == 12_000
    assert options.retry_options.attempts == 1


async def test_create_text_sends_a_plain_prompt_and_reads_output_text(settings):
    client, stub = make(settings, interaction(text="a watercolour style"))

    result = await client.create_text(prompt="define a style",
                                      previous_interaction_id="i-1")

    call = stub.interactions.calls[0]
    assert call["model"] == settings.text_model
    assert call["input"] == "define a style"
    assert call["previous_interaction_id"] == "i-1"
    assert result.text == "a watercolour style"
    assert result.interaction_id == "interactions/abc"


async def test_create_text_falls_back_to_the_last_step_when_output_text_is_empty(settings):
    """The notebook uses both accessors: cell 32 output_text, cells 37/41
    steps[-1].content[0].text. The spike settled output_text as primary; the
    other stays as a fallback."""
    steps = [SimpleNamespace(type="model_output",
                             content=[SimpleNamespace(type="text", text="from the step")])]
    client, _ = make(settings, interaction(text=None, steps=steps))

    assert (await client.create_text(prompt="p")).text == "from the step"


async def test_a_document_becomes_a_multipart_input(settings):
    client, stub = make(settings, interaction(text="ok"))

    await client.create_text(prompt="here is a book", document_uri="files/uploaded-123")

    assert stub.interactions.calls[0]["input"] == [
        {"type": "text", "text": "here is a book"},
        {"type": "document", "uri": "files/uploaded-123"},
    ]


async def test_create_structured_sets_response_format_with_max_items(settings):
    client, stub = make(settings, interaction(text='[{"name":"Toad","prompt":"p"}]'))

    result = await client.create_structured(
        prompt="characters", item_schema={"type": "object"}, max_items=2)

    assert stub.interactions.calls[0]["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": {"type": "array", "maxItems": 2, "items": {"type": "object"}},
    }
    assert result.items == [{"name": "Toad", "prompt": "p"}]


async def test_create_image_prefers_output_image(settings):
    image = SimpleNamespace(data=base64.b64encode(PNG).decode(), mime_type="image/png")
    client, _ = make(settings, interaction(image=image))

    result = await client.create_image(prompt="draw a toad")

    assert result.data == PNG
    assert result.mime_type == "image/png"


async def test_create_image_falls_back_to_walking_the_steps(settings):
    content = SimpleNamespace(type="image", data=base64.b64encode(PNG).decode(),
                              mime_type="image/png")
    steps = [SimpleNamespace(type="model_output", content=[content])]
    client, _ = make(settings, interaction(image=None, steps=steps))

    assert (await client.create_image(prompt="draw a toad")).data == PNG


async def test_an_image_call_with_no_image_anywhere_is_an_error(settings):
    client, _ = make(settings, interaction(image=None, steps=[]))
    with pytest.raises(GeminiError, match="no image"):
        await client.create_image(prompt="draw a toad")


async def test_reference_images_and_system_instruction_are_sent_inline(settings):
    image = SimpleNamespace(data=base64.b64encode(PNG).decode(), mime_type="image/png")
    client, stub = make(settings, interaction(image=image))

    await client.create_image(prompt="use these",
                              reference_images=[ReferenceImage(PNG, "image/png")],
                              system_instruction="no text on the image")

    call = stub.interactions.calls[0]
    assert call["input"] == [
        {"type": "text", "text": "use these"},
        {"type": "image", "data": base64.b64encode(PNG).decode(), "mime_type": "image/png"},
    ]
    assert call["system_instruction"] == "no text on the image"
    assert "previous_interaction_id" not in call


async def test_a_missing_interaction_becomes_InteractionNotFound(settings):
    """Q9: an expired or nonexistent chain head is a 400 invalid_request - the
    provider does not distinguish it from any other malformed request, so the
    chain-head gate below is what makes this predicate safe."""
    error = provider_error("BadRequestError", 400, "invalid_request",
                           "Request contains an invalid argument.")
    client, _ = make(settings, error)

    with pytest.raises(InteractionNotFound):
        await client.create_text(prompt="p", previous_interaction_id="i-gone")


async def test_a_bad_request_without_a_chain_head_is_never_expiry(settings):
    """The had_previous_interaction gate: a 400 on a chainless call cannot be
    an expired head by definition, and must stay a plain GeminiError."""
    error = provider_error("BadRequestError", 400, "invalid_request",
                           "Request contains an invalid argument.")
    client, _ = make(settings, error)

    with pytest.raises(GeminiError) as excinfo:
        await client.create_text(prompt="p")
    assert not isinstance(excinfo.value, InteractionNotFound)


async def test_a_404_with_a_chain_head_is_also_expiry(settings):
    """Never observed live, but compat_errors defines NotFoundError for 404 and
    the provider may start using it; the branch is kept deliberately."""
    error = provider_error("NotFoundError", 404, "not_found", "Interaction not found")
    client, _ = make(settings, error)

    with pytest.raises(InteractionNotFound):
        await client.create_text(prompt="p", previous_interaction_id="i-gone")


async def test_any_other_provider_error_becomes_GeminiError(settings):
    """The 403 permission_denied flake the spike measured at 36.4% - it must
    surface as a plain failed step for the user to retry, never an auto-retry."""
    error = provider_error("PermissionDeniedError", 403, "permission_denied",
                           "The caller does not have permission")
    client, _ = make(settings, error)

    with pytest.raises(GeminiError) as excinfo:
        await client.create_text(prompt="p", previous_interaction_id="i-1")
    assert not isinstance(excinfo.value, InteractionNotFound)


async def test_upload_returns_the_file_uri(settings, tmp_path):
    book = tmp_path / "book.txt"
    book.write_text("text", encoding="utf-8")
    client, _ = make(settings, interaction(text="ok"))

    assert await client.upload_book(book) == "files/uploaded-123"


async def test_a_seeding_call_tolerates_a_response_with_no_image(settings):
    """Observed live: the image model answers a setup instruction in prose -
    "Great! I understand the style and rules you're looking for..." - with
    output_image None, and only the next chained call returns a picture.
    Notebook cell 35 agrees: it keeps the seed's .id and never extracts an image.

    Demanding an image here failed step 3 before a single portrait was attempted.
    UAT caught it; the fake had hidden it by returning a PNG regardless.
    """
    client, _ = make(settings, interaction(text="Great! I understand the style.",
                                           image=None, steps=[]))

    result = await client.create_image(prompt="seed the chain", expect_image=False)

    assert result.interaction_id == "interactions/abc"
    assert result.data == b""
    assert result.mime_type == ""


async def test_a_generating_call_still_requires_an_image(settings):
    """The tolerance is scoped to seeds. A call that should produce a picture and
    does not is still a failure, not a silently empty artifact."""
    client, _ = make(settings, interaction(text="sorry, no", image=None, steps=[]))

    with pytest.raises(GeminiError, match="no image"):
        await client.create_image(prompt="draw a toad")
