from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from app.steps import ProjectStatus, StepName, StepState

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

DisplayStatus = Literal["Draft", "In progress", "Done"]
ImageState = Literal["ready", "generating", "pending"]
FailureCode = Literal["GEMINI_ERROR", "INVALID_OUTPUT", "INTERNAL"]


# ---- requests -------------------------------------------------------------

class SessionCreate(BaseModel):
    name: Annotated[str, Field(min_length=1)]
    email: Annotated[str, Field(min_length=3)]

    @field_validator("name")
    @classmethod
    def _non_blank_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        lowered = value.strip().lower()
        if not EMAIL_RE.match(lowered):
            raise ValueError("must be a valid email address")
        return lowered


class ProjectCreate(BaseModel):
    title: Annotated[str, Field(min_length=1)]
    book_text: Annotated[str, Field(min_length=1)]

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("book_text")
    @classmethod
    def _non_blank_book(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class RunRequest(BaseModel):
    """`step` is asserted by the client so a stale tab cannot silently run the
    step that happens to be current now (design 8)."""
    step: StepName
    style: str | None = None


# ---- responses ------------------------------------------------------------

class SessionView(BaseModel):
    user_id: str
    name: str
    email: str


class Failure(BaseModel):
    code: FailureCode
    message: str


class EntityView(BaseModel):
    id: str
    position: int
    name: str
    prompt: str
    image_url: str | None
    image_state: ImageState


class ProjectListItem(BaseModel):
    id: str
    title: str
    created_at: str
    status: ProjectStatus
    current_step: StepName | None
    display_status: DisplayStatus
    needs_attention: bool
    is_interrupted: bool
    completed_steps: int


class ProjectView(BaseModel):
    id: str
    title: str
    created_at: str
    status: ProjectStatus
    step_state: StepState
    current_step: StepName | None
    display_status: DisplayStatus
    needs_attention: bool
    is_interrupted: bool
    completed_steps: int
    style_text: str | None
    book_excerpt: str
    failure: Failure | None
    characters: list[EntityView]
    chapters: list[EntityView]


class BookView(BaseModel):
    text: str


class ApiError(BaseModel):
    code: str
    message: str


class ApiErrorBody(BaseModel):
    error: ApiError


class RunAccepted(BaseModel):
    project: ProjectView


class RunConflict(BaseModel):
    """A 409 carries the truth as well as the complaint, so the losing caller
    renders current state with no follow-up fetch (design 8)."""
    error: ApiError
    project: ProjectView


def state_message(project: ProjectView) -> dict:
    """The one WebSocket payload shape (design 9.1)."""
    return {"type": "project.state", "project": project.model_dump(mode="json")}
