"""Consume each audition cell once and recover only its exact published manifest."""

from typing import assert_never

from pydantic import ValidationError

from voice_of_karina.audition_checks import (
    check_trial,
    profile_for,
    refresh,
    verify_artifact,
    verify_profiles,
    with_error,
)
from voice_of_karina.audition_models import AuditionResult, AuditionTrial
from voice_of_karina.audition_storage import AuditionStore
from voice_of_karina.contracts import GeneratedAudio, GenerateRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.workflow_models import Adapters


def _save_trial(
    state: AuditionResult, trial: AuditionTrial, storage: AuditionStore
) -> AuditionResult:
    return storage.save(
        state.model_copy(
            update={"trials": tuple(trial if old.id == trial.id else old for old in state.trials)}
        )
    )


def _recover(
    state: AuditionResult, trial: AuditionTrial, storage: AuditionStore, adapters: Adapters
) -> AuditionTrial:
    try:
        manifest = storage.path(state.job_id, "trials", trial.id, f"{trial.message.id}.audio.json")
        audio = GeneratedAudio.model_validate_json(manifest.read_bytes())
        verify_artifact(state, trial, audio, storage)
        if trial.audio is not None and trial.audio != audio:
            return with_error(
                trial, "changed_manifest", "The trial manifest differs from the persisted audio."
            ).model_copy(update={"status": "failed"})
    except (OSError, ValidationError, VoiceError) as error:
        return with_error(trial, "consumed_trial", str(error)).model_copy(
            update={"status": "failed"}
        )
    return check_trial(state, trial.model_copy(update={"audio": audio}), storage, adapters)


def _generate(
    state: AuditionResult, trial: AuditionTrial, storage: AuditionStore, adapters: Adapters
) -> AuditionResult:
    started = trial.model_copy(update={"attempts": 1, "status": "started"})
    state = _save_trial(state, started, storage)
    try:
        directory = storage.path(state.job_id, "trials", trial.id)
        directory.mkdir(parents=True, exist_ok=False)
        request = GenerateRequest(
            mode="reuse",
            voice_id=trial.voice_id,
            messages=(trial.message,),
            language=state.request.language,
            settings=trial.settings,
            max_attempts=1,
            quality=state.request.quality,
        )
        outputs = adapters.synthesize(
            request, profile_for(state, trial.voice_id).reference, directory
        )
        if len(outputs) != 1:
            failed = with_error(
                started, "invalid_output", "An audition trial must return exactly one utterance."
            )
            return _save_trial(state, failed.model_copy(update={"status": "failed"}), storage)
        verify_artifact(state, started, outputs[0], storage)
        started = started.model_copy(update={"audio": outputs[0]})
        state = _save_trial(state, started, storage)
        checked = check_trial(state, started, storage, adapters)
    except (VoiceError, OSError) as error:
        failed = with_error(started, "generation_failed", str(error))
        checked = _recover(state, failed, storage, adapters)
    return _save_trial(state, checked, storage)


def run_trials(state: AuditionResult, storage: AuditionStore, adapters: Adapters) -> AuditionResult:
    """A consumed interrupted trial is recovered or failed, never synthesized again."""
    verify_profiles(state, storage)
    for trial in state.trials:
        match trial.status:
            case "pending":
                state = _generate(state, trial, storage, adapters)
            case "started":
                state = _save_trial(state, _recover(state, trial, storage, adapters), storage)
            case "passed" | "failed" | "needs_input":
                state = _save_trial(state, check_trial(state, trial, storage, adapters), storage)
            case unreachable:
                assert_never(unreachable)
    return storage.save(refresh(state, storage))
