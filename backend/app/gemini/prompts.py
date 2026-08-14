"""Prompt constants taken from Book_illustration.ipynb.

Every string is the notebook's, verbatim, except the two cap additions the
assessment requires. Keeping them as named constants is what makes the
call-sequence acceptance test a test of the notebook's pipeline rather than of
our paraphrase of it (design 7.8).
"""
from __future__ import annotations

from app.steps import MAX_CHARACTERS, MAX_CHAPTERS

# cell 27
BOOK_INTRO = (
    "Here's a book, to illustrate using Nano Banana. "
    "Don't say anything for now, instructions will follow."
)

# The standalone reconstruction path combines the intro with an instruction in
# one call, where "don't say anything" would contradict the instruction.
BOOK_INTRO_STANDALONE = "Here's a book, to illustrate using Nano Banana."

# cell 23 - system_instructions
RULES = (
    "\n"
    "  There must be no text on the image, it should not look like a cover page.\n"
    "  It should be an full illustration with no borders, titles, nor description.\n"
    "  Unless asked otherwise, stay family-friendly with uplifting colors.\n"
    "  Each produced should be a simple image, no panels.\n"
)

# cell 30 - both branches. The typos ("art syle", "furture") are the notebook's.
STYLE_GENERATE = (
    "Can you define a art style that would fit the story but with a twist? "
    "Just give us the prompt for the art syle that will added to the furture prompts."
)
STYLE_ACKNOWLEDGE = (
    'The art style will be:"{style}". Keep that in mind when generating future '
    "prompts. Keep quiet for now, instructions will follow."
)
STYLE_WRAPPER = 'Follow this style: "{style}" '

# cell 32, plus the cap the assessment moves onto the list itself
CHARACTERS_INSTRUCTION = (
    "Can you describe the main characters (only the adults) and prepare a prompt "
    "describing them with as much details as possible (use the descriptions from the "
    "book) so Nano Banana can generate images of them? Each prompt should be at least "
    f"50 words. Return at most {MAX_CHARACTERS} characters."
)

# cell 35
IMAGE_SEED = (
    "\n"
    "      You are going to generate portrait images to illustrate {title}.\n"
    "      The style we want you to follow is: {style}\n"
    "      Also follow those rules: {rules}\n"
    "    "
)
PORTRAIT_INSTRUCTION = (
    "Create an illustration for {name} following this description: {prompt}"
)

# cell 37, plus the cap
CHAPTERS_INSTRUCTION = (
    "Now, for each chapters of the book, give me a prompt to illustrate what happens "
    "in it. It should be a single image, not a multi-tiled page. Be very descriptive, "
    "especially of the characters. Be very descriptive and remember to tell their name "
    "and to reuse the character prompts if they appear in the images. Also list all "
    f"characters who appear in it. Return at most {MAX_CHAPTERS} chapter."
)

# cell 38
CHAPTER_SEED = (
    "Starting from now, we're going to illustrate the book's chapters. Don't forget to "
    "refer to your previous illustrations of the characters to keep the characters "
    "consistency, but feel free to change their position."
)
ILLUSTRATION_INSTRUCTION = (
    "Create an illustration for {name} using the previously generated characters "
    "following this description: {prompt}"
)

# cell 44 - the standalone image call used when the image chain is unusable
ILLUSTRATION_STANDALONE = (
    "\n"
    "              Create this illustration for {name}:\n"
    "                {prompt}\n"
    "              Use the provided images as references of what the characters look like.\n"
    "          "
)


def characters_standalone(style_text: str) -> str:
    return (
        f"{BOOK_INTRO_STANDALONE}\n"
        f"{STYLE_WRAPPER.format(style=style_text)}\n"
        f"{CHARACTERS_INSTRUCTION}"
    )


def chapters_standalone(style_text: str, character_prompts: list[str]) -> str:
    described = "\n".join(f"- {p}" for p in character_prompts)
    return (
        f"{BOOK_INTRO_STANDALONE}\n"
        f"{STYLE_WRAPPER.format(style=style_text)}\n"
        f"The characters already illustrated are:\n{described}\n"
        f"{CHAPTERS_INSTRUCTION}"
    )
