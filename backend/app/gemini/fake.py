from __future__ import annotations


class FakeGeminiClient:  # replaced in full by Task 17
    """Minimal stand-in so Task 10's fixtures import.

    Task 11's project-creation test asserts ``fake_gemini.calls == []`` to
    prove that creating a project makes zero Gemini calls, so this stand-in
    needs a real, empty ``calls`` list rather than a bare ``pass`` body.
    """

    def __init__(self) -> None:
        self.calls: list = []
