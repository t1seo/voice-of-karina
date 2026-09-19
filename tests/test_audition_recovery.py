from pathlib import Path

import pytest

from tests.audition_helpers import AuditionBackend, audition_request
from voice_of_karina.audition_storage import AuditionStore
from voice_of_karina.audition_workflow import AuditionWorkflow
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store


@pytest.mark.parametrize("published", [False, True])
def test_interrupted_trial_is_recovered_or_failed_without_another_synthesis(
    tmp_path: Path, published: bool
) -> None:
    # Given
    store = Store(tmp_path / "state")
    request = audition_request(store, tmp_path)
    storage = AuditionStore(store)
    backend = AuditionBackend(crash_at=1, write_before_crash=published)
    workflow = AuditionWorkflow(store, backend.adapters())
    with pytest.raises(KeyboardInterrupt):
        _ = workflow.audition(request)
    interrupted = storage.load(storage.identity(request))
    assert interrupted.trials[0].attempts == 1
    assert interrupted.trials[0].status == "started"
    # When
    state = workflow.resume(interrupted.job_id)
    # Then
    assert len(backend.calls) == 4
    assert len(state.choices) == (4 if published else 3)
    assert state.trials[0].status == ("passed" if published else "failed")
    assert all(trial.attempts == 1 for trial in state.trials)


def test_unavailable_asr_revalidates_saved_audio_without_spending_more_synthesis(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path / "state")
    request = audition_request(store, tmp_path)
    backend = AuditionBackend(unavailable=[True])
    workflow = AuditionWorkflow(store, backend.adapters())
    first = workflow.audition(request)
    assert first.status == "needs_input"
    assert len(backend.validations) == 4
    backend.unavailable[0] = False
    # When
    resumed = workflow.resume(first.job_id)
    # Then
    assert resumed.status == "needs_selection"
    assert len(backend.calls) == 4
    assert len(backend.validations) == 8
    assert all(trial.audio is not None for trial in resumed.trials)


def test_status_performs_no_model_calls_even_when_validation_is_held(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    backend = AuditionBackend(unavailable=[True])
    workflow = AuditionWorkflow(store, backend.adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    before = tuple(backend.validations)
    # When
    checked = workflow.status(state.job_id)
    # Then
    assert checked.status == "needs_input"
    assert tuple(backend.validations) == before
    assert len(backend.calls) == 4


def test_concurrent_resume_cannot_allocate_trials_while_job_lock_is_held(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    backend = AuditionBackend()
    workflow = AuditionWorkflow(store, backend.adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    # When
    with store.lock(state.job_id), pytest.raises(VoiceError, match="already running"):
        _ = workflow.resume(state.job_id)
    # Then
    assert len(backend.calls) == 4


def test_frozen_comparison_survives_removal_of_original_registered_reference(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path / "state")
    backend = AuditionBackend()
    workflow = AuditionWorkflow(store, backend.adapters())
    request = audition_request(store, tmp_path)
    state = workflow.audition(request)
    for voice_id in request.voice_ids:
        Path(store.voice(voice_id).reference.audio_path).unlink()
    # When
    resumed = workflow.resume(state.job_id)
    # Then
    assert resumed.status == "needs_selection"
    assert len(resumed.choices) == 4
    assert len(backend.calls) == 4
