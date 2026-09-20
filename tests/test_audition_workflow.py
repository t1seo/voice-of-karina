from pathlib import Path

import pytest

from tests.audition_helpers import AuditionBackend, audition_request
from voice_of_karina.audition_workflow import AuditionWorkflow
from voice_of_karina.contracts import Message
from voice_of_karina.curation_contracts import SelectVoiceRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store


def test_fixed_matrix_replays_without_replenishing_synthesis_budget(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    request = audition_request(store, tmp_path)
    backend = AuditionBackend()
    workflow = AuditionWorkflow(store, backend.adapters())
    first = workflow.audition(request)
    reordered = request.model_copy(
        update={
            "voice_ids": tuple(reversed(request.voice_ids)),
            "seeds": tuple(reversed(request.seeds)),
            "settings": request.settings.model_copy(update={"seed": 42}),
        }
    )
    # When
    replay = workflow.audition(reordered)
    # Then
    assert replay == first
    assert first.status == "needs_selection"
    assert len(first.trials) == len(backend.calls) == 4
    assert len(first.choices) == 4
    assert all(trial.attempts == 1 for trial in first.trials)
    assert all(call.max_attempts == 1 for call in backend.calls)
    assert all(len(call.messages) == 1 for call in backend.calls)


def test_choice_requires_every_preview_text_to_pass_for_one_reference_and_seed(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path / "state")
    request = audition_request(store, tmp_path).model_copy(
        update={
            "messages": (Message(id="good", text="좋아요."), Message(id="bad", text="안 돼요."))
        }
    )
    backend = AuditionBackend(rejected_texts=("안 돼요.",))
    # When
    state = AuditionWorkflow(store, backend.adapters()).audition(request)
    # Then
    assert state.status == "failed"
    assert state.choices == ()
    assert len(backend.calls) == 8
    assert sum(trial.status == "failed" for trial in state.trials) == 4


def test_unknown_status_and_resume_do_not_create_phantom_jobs(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    workflow = AuditionWorkflow(store, AuditionBackend().adapters())
    # When
    with pytest.raises(VoiceError, match="No saved audition"):
        _ = workflow.status("audition-unknown")
    with pytest.raises(VoiceError, match="No saved audition"):
        _ = workflow.resume("audition-unknown")
    # Then
    assert not store.job_dir("audition-unknown").exists()


def test_selection_saves_exact_recipe_and_replays_without_replacing_review(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    request = audition_request(store, tmp_path)
    workflow = AuditionWorkflow(store, AuditionBackend().adapters())
    state = workflow.audition(request)
    choice = state.choices[0]
    selection = SelectVoiceRequest(
        job_id=state.job_id,
        choice_id=choice.choice_id,
        name="Selected voice",
        reason="The compared preview has clearer pronunciation.",
    )
    first = workflow.select(selection)
    # When
    replay = workflow.select(selection.model_copy(update={"name": "Changed", "reason": "Changed"}))
    # Then
    assert first == replay
    assert first.voice.recipe == choice.recipe
    assert first.voice.selection is not None
    assert first.voice.selection.reason == selection.reason
    assert workflow.status(state.job_id).status == "complete"


def test_finalized_selection_cannot_be_replaced_by_another_eligible_choice(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    workflow = AuditionWorkflow(store, AuditionBackend().adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    request = SelectVoiceRequest(
        job_id=state.job_id, choice_id=state.choices[0].choice_id, name="Voice", reason="Reviewed"
    )
    _ = workflow.select(request)
    # When
    with pytest.raises(VoiceError, match="already selected"):
        _ = workflow.select(request.model_copy(update={"choice_id": state.choices[1].choice_id}))
