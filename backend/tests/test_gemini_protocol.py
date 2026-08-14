import importlib

import pytest

from app import steps
from app.gemini import prompts
from app.gemini.protocol import InvalidStructuredOutput, parse_items


def test_book_intro_is_the_notebook_cell_27_text_verbatim():
    assert prompts.BOOK_INTRO == (
        "Here's a book, to illustrate using Nano Banana. "
        "Don't say anything for now, instructions will follow."
    )


def test_rules_are_the_notebook_cell_23_system_instructions_verbatim():
    assert prompts.RULES == (
        "\n"
        "  There must be no text on the image, it should not look like a cover page.\n"
        "  It should be an full illustration with no borders, titles, nor description.\n"
        "  Unless asked otherwise, stay family-friendly with uplifting colors.\n"
        "  Each produced should be a simple image, no panels.\n"
    )


def test_style_prompts_are_the_notebook_cell_30_branches_verbatim():
    assert prompts.STYLE_GENERATE == (
        "Can you define a art style that would fit the story but with a twist? "
        "Just give us the prompt for the art syle that will added to the furture prompts."
    )
    assert prompts.STYLE_ACKNOWLEDGE.format(style="watercolour") == (
        'The art style will be:"watercolour". Keep that in mind when generating '
        'future prompts. Keep quiet for now, instructions will follow.'
    )


def test_the_style_wrapper_is_applied_at_the_point_of_use_not_at_persistence():
    assert prompts.STYLE_WRAPPER.format(style="watercolour") == \
        'Follow this style: "watercolour" '


def test_characters_instruction_keeps_the_notebook_text_and_adds_the_cap():
    """Assessment 03 moves the cap onto the list itself, so Gemini's own context
    never holds a character we would discard (design 2, contradiction 1)."""
    assert prompts.CHARACTERS_INSTRUCTION.startswith(
        "Can you describe the main characters (only the adults) and prepare a prompt "
        "describing them with as much details as possible (use the descriptions from "
        "the book) so Nano Banana can generate images of them? "
        "Each prompt should be at least 50 words."
    )
    assert "at most 2" in prompts.CHARACTERS_INSTRUCTION


def test_chapters_instruction_keeps_the_notebook_text_and_adds_the_cap():
    assert prompts.CHAPTERS_INSTRUCTION.startswith(
        "Now, for each chapters of the book, give me a prompt to illustrate what "
        "happens in it. It should be a single image, not a multi-tiled page."
    )
    assert "at most 1" in prompts.CHAPTERS_INSTRUCTION


def test_cap_instructions_follow_the_canonical_step_limits(monkeypatch):
    """Changing the enforced caps must change the prompts sent to Gemini too."""
    try:
        with monkeypatch.context() as limits:
            limits.setattr(steps, "MAX_CHARACTERS", 7)
            limits.setattr(steps, "MAX_CHAPTERS", 5)
            reloaded_prompts = importlib.reload(prompts)

            assert "Return at most 7 characters." in reloaded_prompts.CHARACTERS_INSTRUCTION
            assert "Return at most 5 chapter." in reloaded_prompts.CHAPTERS_INSTRUCTION
    finally:
        # Restoring the module matters because its constants are built at import time.
        importlib.reload(prompts)


def test_the_image_seed_takes_the_title_from_the_project():
    seeded = prompts.IMAGE_SEED.format(title="The Wind in the Willows",
                                       style='Follow this style: "x" ', rules=prompts.RULES)
    assert "The Wind in the Willows" in seeded
    assert "You are going to generate portrait images to illustrate" in seeded
    # The notebook's stray "# TODO: Sysyem instructions" comment lands inside its
    # f-string. It is a typo'd note, not an instruction, and is dropped.
    assert "TODO" not in seeded


def test_chapter_seed_and_illustration_prompts_are_the_notebook_cell_38_text():
    assert prompts.CHAPTER_SEED == (
        "Starting from now, we're going to illustrate the book's chapters. "
        "Don't forget to refer to your previous illustrations of the characters to "
        "keep the characters consistency, but feel free to change their position."
    )
    assert prompts.ILLUSTRATION_INSTRUCTION.format(name="Ch1", prompt="a river") == (
        "Create an illustration for Ch1 using the previously generated characters "
        "following this description: a river"
    )


def test_portrait_instruction_is_the_notebook_cell_35_text():
    assert prompts.PORTRAIT_INSTRUCTION.format(name="Toad", prompt="a stout toad") == (
        "Create an illustration for Toad following this description: a stout toad"
    )


def test_parse_items_returns_the_decoded_array():
    assert parse_items('[{"name":"Toad","prompt":"p"}]') == [{"name": "Toad", "prompt": "p"}]


def test_parse_items_rejects_malformed_json():
    with pytest.raises(InvalidStructuredOutput):
        parse_items("not json at all")


def test_parse_items_rejects_a_non_array_top_level():
    with pytest.raises(InvalidStructuredOutput):
        parse_items('{"name":"Toad"}')


def test_parse_items_rejects_empty_output():
    with pytest.raises(InvalidStructuredOutput):
        parse_items("")
