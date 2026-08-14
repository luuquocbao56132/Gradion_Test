from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

MAX_CHARACTERS = 2
MAX_CHAPTERS = 1


class StepName(StrEnum):
    STYLE = "STYLE"
    CHARACTERS = "CHARACTERS"
    PORTRAITS = "PORTRAITS"
    CHAPTERS = "CHAPTERS"
    ILLUSTRATIONS = "ILLUSTRATIONS"


class ProjectStatus(StrEnum):
    CREATED = "CREATED"
    STYLE_SET = "STYLE_SET"
    CHARACTERS_GENERATED = "CHARACTERS_GENERATED"
    PORTRAITS_GENERATED = "PORTRAITS_GENERATED"
    CHAPTERS_GENERATED = "CHAPTERS_GENERATED"
    DONE = "DONE"


class StepState(StrEnum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class StepDef:
    name: StepName
    label: str
    status_before: ProjectStatus
    status_after: ProjectStatus
    chain: Literal["text", "image"]


# The whole pipeline definition. Adding a sixth step is one entry here plus one
# handler; the orchestration never changes (design 3).
STEPS: list[StepDef] = [
    StepDef(StepName.STYLE, "Style", ProjectStatus.CREATED, ProjectStatus.STYLE_SET, "text"),
    StepDef(StepName.CHARACTERS, "Characters", ProjectStatus.STYLE_SET,
            ProjectStatus.CHARACTERS_GENERATED, "text"),
    StepDef(StepName.PORTRAITS, "Portraits", ProjectStatus.CHARACTERS_GENERATED,
            ProjectStatus.PORTRAITS_GENERATED, "image"),
    StepDef(StepName.CHAPTERS, "Chapters", ProjectStatus.PORTRAITS_GENERATED,
            ProjectStatus.CHAPTERS_GENERATED, "text"),
    StepDef(StepName.ILLUSTRATIONS, "Illustrations", ProjectStatus.CHAPTERS_GENERATED,
            ProjectStatus.DONE, "image"),
]

_BY_NAME = {s.name: s for s in STEPS}
_STATUS_ORDER = [ProjectStatus.CREATED] + [s.status_after for s in STEPS]


def step_def(step: StepName) -> StepDef:
    return _BY_NAME[StepName(step)]


def current_step(status: ProjectStatus) -> StepName | None:
    index = _STATUS_ORDER.index(ProjectStatus(status))
    return STEPS[index].name if index < len(STEPS) else None


def completed_steps(status: ProjectStatus) -> int:
    return _STATUS_ORDER.index(ProjectStatus(status))


def status_before(step: StepName) -> ProjectStatus:
    return step_def(step).status_before


def status_after(step: StepName) -> ProjectStatus:
    return step_def(step).status_after


def chain_of_step(step: StepName) -> Literal["text", "image"]:
    return step_def(step).chain


def display_status(status: ProjectStatus, step_state: StepState) -> str:
    """Exactly the three values assessment 4.4 names. Failure and interruption
    are carried by needs_attention, not by a fourth pill (design 4.2)."""
    if ProjectStatus(status) is ProjectStatus.DONE:
        return "Done"
    if ProjectStatus(status) is ProjectStatus.CREATED and StepState(step_state) is StepState.IDLE:
        return "Draft"
    return "In progress"


def needs_attention(step_state: StepState, is_interrupted: bool) -> bool:
    return StepState(step_state) is StepState.FAILED or bool(is_interrupted)
