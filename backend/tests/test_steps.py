import pytest

from app.steps import (
    MAX_CHAPTERS, MAX_CHARACTERS, STEPS, ProjectStatus, StepName, StepState,
    chain_of_step, completed_steps, current_step, display_status,
    needs_attention, status_after, status_before,
)


def test_the_five_steps_are_in_notebook_order():
    assert [s.name for s in STEPS] == [
        StepName.STYLE, StepName.CHARACTERS, StepName.PORTRAITS,
        StepName.CHAPTERS, StepName.ILLUSTRATIONS,
    ]


def test_step_labels_match_the_demo():
    assert [s.label for s in STEPS] == [
        "Style", "Characters", "Portraits", "Chapters", "Illustrations"]


def test_caps_are_the_assessment_values():
    assert (MAX_CHARACTERS, MAX_CHAPTERS) == (2, 1)


@pytest.mark.parametrize(
    "status,expected",
    [
        (ProjectStatus.CREATED, StepName.STYLE),
        (ProjectStatus.STYLE_SET, StepName.CHARACTERS),
        (ProjectStatus.CHARACTERS_GENERATED, StepName.PORTRAITS),
        (ProjectStatus.PORTRAITS_GENERATED, StepName.CHAPTERS),
        (ProjectStatus.CHAPTERS_GENERATED, StepName.ILLUSTRATIONS),
        (ProjectStatus.DONE, None),
    ],
)
def test_current_step_is_derived_from_status(status, expected):
    assert current_step(status) == expected


@pytest.mark.parametrize(
    "status,count",
    [
        (ProjectStatus.CREATED, 0),
        (ProjectStatus.STYLE_SET, 1),
        (ProjectStatus.CHARACTERS_GENERATED, 2),
        (ProjectStatus.PORTRAITS_GENERATED, 3),
        (ProjectStatus.CHAPTERS_GENERATED, 4),
        (ProjectStatus.DONE, 5),
    ],
)
def test_completed_steps_counts_finished_steps(status, count):
    assert completed_steps(status) == count


def test_status_before_and_after_are_inverse_along_the_chain():
    for step in StepName:
        assert current_step(status_before(step)) == step
        assert completed_steps(status_after(step)) == completed_steps(status_before(step)) + 1


@pytest.mark.parametrize(
    "status,state,expected",
    [
        (ProjectStatus.DONE, StepState.IDLE, "Done"),
        (ProjectStatus.CREATED, StepState.IDLE, "Draft"),
        (ProjectStatus.CREATED, StepState.RUNNING, "In progress"),
        (ProjectStatus.CREATED, StepState.FAILED, "In progress"),
        (ProjectStatus.STYLE_SET, StepState.IDLE, "In progress"),
        (ProjectStatus.CHAPTERS_GENERATED, StepState.FAILED, "In progress"),
    ],
)
def test_display_status_uses_only_the_three_assessment_values(status, state, expected):
    assert display_status(status, state) == expected


def test_no_fourth_pill_value_can_be_produced():
    produced = {display_status(s, st) for s in ProjectStatus for st in StepState}
    assert produced == {"Draft", "In progress", "Done"}


@pytest.mark.parametrize(
    "state,interrupted,expected",
    [
        (StepState.IDLE, False, False),
        (StepState.RUNNING, False, False),
        (StepState.FAILED, False, True),
        (StepState.RUNNING, True, True),
    ],
)
def test_needs_attention_is_separate_from_the_pill(state, interrupted, expected):
    assert needs_attention(state, interrupted) is expected


def test_text_and_image_chains_are_assigned_per_the_design():
    assert chain_of_step(StepName.STYLE) == "text"
    assert chain_of_step(StepName.CHARACTERS) == "text"
    assert chain_of_step(StepName.PORTRAITS) == "image"
    assert chain_of_step(StepName.CHAPTERS) == "text"
    assert chain_of_step(StepName.ILLUSTRATIONS) == "image"
