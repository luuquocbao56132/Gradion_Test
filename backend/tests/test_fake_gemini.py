import asyncio
from pathlib import Path

import pytest

from app.gemini.fake import FakeGeminiClient
from app.gemini.protocol import GeminiError, InteractionNotFound, InvalidStructuredOutput


async def test_text_output_is_deterministic_and_recorded():
    fake = FakeGeminiClient()
    first = await fake.create_text(prompt="define a style")
    second = await fake.create_text(prompt="define a style")
    assert first.text == second.text == FakeGeminiClient.STYLE_TEXT
    assert first.interaction_id != second.interaction_id
    assert [c.kind for c in fake.calls] == ["text", "text"]
    assert fake.calls[0].prompt == "define a style"


async def test_the_recorder_captures_everything_the_assertions_need():
    fake = FakeGeminiClient()
    await fake.create_structured(prompt="characters", previous_interaction_id="i-1",
                                 document_uri="files/abc", item_schema={"type": "object"},
                                 max_items=2)
    call = fake.calls[0]
    assert call.kind == "structured"
    assert call.previous_interaction_id == "i-1"
    assert call.document_uri == "files/abc"
    assert call.max_items == 2


async def test_structured_output_respects_the_requested_cap():
    fake = FakeGeminiClient()
    result = await fake.create_structured(prompt="p", item_schema={}, max_items=2)
    assert [i["name"] for i in result.items] == ["Toad", "Ratty"]
    chapters = await fake.create_structured(prompt="p", item_schema={}, max_items=1)
    assert [i["name"] for i in chapters.items] == ["Chapter One"]


async def test_images_are_a_tiny_valid_png():
    fake = FakeGeminiClient()
    image = await fake.create_image(prompt="draw a toad")
    assert image.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.mime_type == "image/png"


async def test_upload_returns_a_uri_and_is_recorded():
    fake = FakeGeminiClient()
    uri = await fake.upload_book(Path("book.txt"))
    assert uri.startswith("files/")
    assert [c.kind for c in fake.calls] == ["upload"]


async def test_a_failure_can_be_injected_at_a_chosen_call():
    fake = FakeGeminiClient()
    fake.fail_on(1, GeminiError("the provider said no"))
    await fake.create_image(prompt="first")
    with pytest.raises(GeminiError, match="the provider said no"):
        await fake.create_image(prompt="second")


async def test_interaction_not_found_can_be_injected():
    fake = FakeGeminiClient()
    fake.fail_on(0, InteractionNotFound("interaction expired"))
    with pytest.raises(InteractionNotFound):
        await fake.create_text(prompt="p", previous_interaction_id="i-gone")


async def test_a_schema_violating_response_can_be_injected():
    fake = FakeGeminiClient()
    fake.invalid_json_on(0)
    with pytest.raises(InvalidStructuredOutput):
        await fake.create_structured(prompt="p", item_schema={}, max_items=2)


async def test_extra_items_lets_a_test_exceed_the_cap_on_purpose():
    fake = FakeGeminiClient()
    fake.extra_items = 3
    result = await fake.create_structured(prompt="p", item_schema={}, max_items=2)
    assert len(result.items) == 3


async def test_a_gated_call_is_observable_while_held_and_released_explicitly():
    fake = FakeGeminiClient()
    fake.hold_from(0)
    task = asyncio.create_task(fake.create_image(prompt="slow"))
    await fake.wait_for_calls(1)
    assert not task.done()
    fake.release()
    assert (await task).mime_type == "image/png"


async def test_wait_for_calls_times_out_rather_than_hanging_a_suite():
    fake = FakeGeminiClient()
    with pytest.raises(TimeoutError):
        await fake.wait_for_calls(1, timeout=0.05)
