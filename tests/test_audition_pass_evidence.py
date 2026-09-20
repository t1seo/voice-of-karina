from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.audition_helpers import AuditionBackend, audition_request
from voice_of_karina import audition_checks
from voice_of_karina.audition_models import AuditionResult, AuditionTrial
from voice_of_karina.audition_storage import AuditionStore
from voice_of_karina.audition_workflow import AuditionWorkflow
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store

type Fault = Literal["audio", "quality", "synthesis", "invalid_quality", "retry_quality"]


def without_pass_evidence(trial: AuditionTrial, fault: Fault) -> AuditionTrial:
    assert trial.audio is not None
    assert trial.quality is not None
    match fault:
        case "audio":
            return trial.model_copy(update={"audio": None})
        case "quality":
            return trial.model_copy(update={"quality": None})
        case "synthesis":
            return trial.model_copy(
                update={"audio": trial.audio.model_copy(update={"synthesis": None})}
            )
        case "invalid_quality":
            return trial.model_copy(
                update={"quality": trial.quality.model_copy(update={"valid": False})}
            )
        case "retry_quality":
            return trial.model_copy(
                update={"quality": trial.quality.model_copy(update={"decision": "retry"})}
            )
        case unreachable:
            assert_never(unreachable)


def corrupted_state(tmp_path: Path, fault: Fault) -> tuple[AuditionResult, AuditionStore]:
    store = Store(tmp_path / "state")
    workflow = AuditionWorkflow(store, AuditionBackend().adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    damaged = state.model_copy(
        update={"trials": tuple(without_pass_evidence(trial, fault) for trial in state.trials)}
    )
    return damaged, AuditionStore(store)


@pytest.mark.parametrize(
    "fault", ["audio", "quality", "synthesis", "invalid_quality", "retry_quality"]
)
def test_persisted_passed_trials_require_complete_structural_evidence(
    tmp_path: Path, fault: Fault
) -> None:
    # Given
    state, storage = corrupted_state(tmp_path, fault)
    _ = storage.save(state)
    # When
    with pytest.raises(VoiceError, match="ledger is damaged"):
        _ = storage.load(state.job_id)


@pytest.mark.parametrize(
    "fault", ["audio", "quality", "synthesis", "invalid_quality", "retry_quality"]
)
def test_choice_derivation_refuses_in_memory_pass_without_evidence(
    tmp_path: Path, fault: Fault
) -> None:
    # Given
    state, _storage = corrupted_state(tmp_path, fault)
    # When
    choices = audition_checks.eligible_choices(state)
    # Then
    assert choices == ()


@pytest.mark.parametrize(
    "fault", ["audio", "quality", "synthesis", "invalid_quality", "retry_quality"]
)
def test_status_cannot_keep_passed_label_when_model_copy_bypasses_validation(
    tmp_path: Path, fault: Fault
) -> None:
    # Given
    state, storage = corrupted_state(tmp_path, fault)
    # When
    checked = audition_checks.refresh(state, storage)
    # Then
    assert checked.choices == ()
    assert all(trial.status == "failed" for trial in checked.trials)


def test_old_policy_passes_load_and_revalidate_existing_audio_without_synthesis(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path / "state")
    storage = AuditionStore(store)
    backend = AuditionBackend()
    workflow = AuditionWorkflow(store, backend.adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    stale = tuple(
        trial.model_copy(
            update={"quality": trial.quality.model_copy(update={"policy_version": "older-policy"})}
        )
        for trial in state.trials
        if trial.quality is not None
    )
    _ = storage.save(state.model_copy(update={"trials": stale}))
    assert storage.load(state.job_id).trials == stale
    assert audition_checks.eligible_choices(storage.load(state.job_id)) == ()
    assert workflow.status(state.job_id).choices == ()
    # When
    resumed = workflow.resume(state.job_id)
    # Then
    assert len(backend.calls) == 4
    assert len(resumed.choices) == 4
    assert all(trial.status == "passed" for trial in resumed.trials)


def test_empty_in_memory_matrix_cannot_create_vacuously_passing_choices(tmp_path: Path) -> None:
    # Given
    state, _storage = corrupted_state(tmp_path, "audio")
    empty = state.model_copy(update={"trials": ()})
    # When
    choices = audition_checks.eligible_choices(empty)
    # Then
    assert choices == ()
