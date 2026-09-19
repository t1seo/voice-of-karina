"""Verify exact comparison artifacts and derive choices without ranking naturalness."""

import hashlib
import math
from pathlib import Path
from typing import Literal, assert_never

from voice_of_karina.audition_models import AuditionChoice, AuditionResult, AuditionTrial
from voice_of_karina.audition_storage import AuditionStore
from voice_of_karina.backends.models import CLONE_MODEL
from voice_of_karina.contracts import FrozenModel, GeneratedAudio, Reference
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import VoiceRecipe
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import file_digest, profile_identity
from voice_of_karina.workflow_models import Adapters, ErrorDetail, VoiceProfile


class _ChoiceEvidence(FrozenModel):
    job_id: str
    reference: Reference
    recipe: VoiceRecipe
    trials: tuple[AuditionTrial, ...]


def profile_for(state: AuditionResult, voice_id: str) -> VoiceProfile:
    """Resolve the profile already proven to belong to the complete trial matrix."""
    return next(profile for profile in state.profiles if profile.id == voice_id)


def verify_profiles(state: AuditionResult, storage: AuditionStore) -> None:
    """Frozen references retain the original immutable profile identity and bytes."""
    for profile in state.profiles:
        expected = storage.path(state.job_id, "references", f"{profile.id}.wav")
        if (
            Path(profile.reference.audio_path) != expected
            or not expected.is_file()
            or file_digest(expected) != profile.reference.sha256
            or profile_identity(profile.reference, profile.recipe, profile.selection) != profile.id
        ):
            raise VoiceError(
                "invalid_reference", "A frozen audition reference is missing or changed."
            )


def verify_artifact(
    state: AuditionResult, trial: AuditionTrial, audio: GeneratedAudio, storage: AuditionStore
) -> None:
    """Bind the exact WAV to its matrix cell, text, reference, model, and applied seed."""
    expected = storage.path(state.job_id, "trials", trial.id, f"{trial.message.id}.wav")
    evidence = audio.synthesis
    if (
        Path(audio.path) != expected
        or not expected.is_file()
        or audio.message_id != trial.message.id
        or audio.reference != profile_for(state, trial.voice_id).reference
        or audio.language != state.request.language
        or audio.model_id != CLONE_MODEL.repository
        or audio.model_revision != CLONE_MODEL.revision
        or audio.sample_rate <= 0
        or not math.isfinite(audio.duration_seconds)
        or audio.duration_seconds <= 0
        or evidence is None
        or evidence.settings != trial.settings
        or evidence.text != trial.message.text
        or evidence.audio_sha256 != file_digest(expected)
    ):
        raise VoiceError(
            "invalid_artifact", "The audition artifact does not match its trial evidence."
        )


def with_error(trial: AuditionTrial, code: str, message: str) -> AuditionTrial:
    """Retain distinct failures without duplicating them on read or resume."""
    error = ErrorDetail(code=code, message=message)
    errors = trial.errors if error in trial.errors else (*trial.errors, error)
    return trial.model_copy(update={"errors": errors})


def check_trial(
    state: AuditionResult,
    trial: AuditionTrial,
    storage: AuditionStore,
    adapters: Adapters | None = None,
) -> AuditionTrial:
    """Revalidate held or stale audio only when explicitly resuming or selecting."""
    incomplete_pass = trial.status == "passed" and not trial.has_pass_evidence()
    if trial.audio is None or incomplete_pass:
        return (
            with_error(
                trial, "missing_pass_evidence", "A passed trial is missing complete pass evidence."
            ).model_copy(update={"status": "failed"})
            if incomplete_pass
            else trial
        )
    try:
        verify_artifact(state, trial, trial.audio, storage)
    except (VoiceError, OSError) as error:
        return with_error(trial, "invalid_artifact", str(error)).model_copy(
            update={"status": "failed"}
        )
    quality = trial.quality
    if (
        quality is None
        or quality.policy_version != QUALITY_POLICY_VERSION
        or quality.decision == "needs_input"
    ):
        if adapters is None:
            return trial.model_copy(update={"status": "needs_input"})
        try:
            quality = adapters.validate(trial.audio, trial.message.text, state.request.quality)
        except (VoiceError, OSError) as error:
            return with_error(trial, "validation_unavailable", str(error)).model_copy(
                update={"status": "needs_input"}
            )
        trial = trial.model_copy(update={"quality": quality})
    if quality.policy_version != QUALITY_POLICY_VERSION:
        return with_error(
            trial, "stale_quality", "The checker did not report the current policy."
        ).model_copy(update={"status": "needs_input"})
    status: Literal["passed", "failed", "needs_input"]
    match quality.decision:
        case "pass":
            status = "passed" if quality.valid else "failed"
        case "retry":
            status = "failed"
        case "needs_input":
            status = "needs_input"
        case unreachable:
            assert_never(unreachable)
    return trial.model_copy(update={"status": status})


def eligible_choices(state: AuditionResult) -> tuple[AuditionChoice, ...]:
    """Derive choices from complete trial evidence checked under the current policy."""
    choices: list[AuditionChoice] = []
    for profile in state.profiles:
        for seed in state.request.seeds:
            trials = tuple(
                trial
                for trial in state.trials
                if trial.voice_id == profile.id and trial.settings.seed == seed
            )
            if tuple(trial.message for trial in trials) != state.request.messages or not all(
                trial.status == "passed"
                and trial.has_pass_evidence()
                and trial.quality is not None
                and trial.quality.policy_version == QUALITY_POLICY_VERSION
                for trial in trials
            ):
                continue
            recipe = VoiceRecipe(
                language=state.request.language,
                settings=state.request.settings.model_copy(update={"seed": seed}),
                model_id=CLONE_MODEL.repository,
                model_revision=CLONE_MODEL.revision,
            )
            evidence = _ChoiceEvidence(
                job_id=state.job_id, reference=profile.reference, recipe=recipe, trials=trials
            )
            choices.append(
                AuditionChoice(
                    choice_id=hashlib.sha256(evidence.model_dump_json().encode()).hexdigest(),
                    voice_id=profile.id,
                    recipe=recipe,
                    trial_ids=tuple(trial.id for trial in trials),
                    audio_sha256=tuple(
                        trial.audio.synthesis.audio_sha256
                        for trial in trials
                        if trial.audio is not None and trial.audio.synthesis is not None
                    ),
                )
            )
    return tuple(choices)


def refresh(
    state: AuditionResult, storage: AuditionStore, adapters: Adapters | None = None
) -> AuditionResult:
    """Derive current eligibility from evidence; metrics never choose the preferred voice."""
    verify_profiles(state, storage)
    checked = tuple(check_trial(state, trial, storage, adapters) for trial in state.trials)
    state = state.model_copy(update={"trials": checked})
    choices = eligible_choices(state)
    if state.selection is not None:
        selected = next(
            (choice for choice in choices if choice.choice_id == state.selection.choice_id), None
        )
        eligible = selected is not None
        if state.selected_voice_id is not None:
            profile = storage.store.voice(state.selected_voice_id)
            if profile.selection != state.selection or (
                selected is not None
                and (
                    profile.recipe != selected.recipe
                    or profile.reference.model_copy(update={"audio_path": ""})
                    != profile_for(state, selected.voice_id).reference.model_copy(
                        update={"audio_path": ""}
                    )
                )
            ):
                raise VoiceError(
                    "invalid_selection", "The saved profile belongs to a different selection."
                )
            status = "complete" if eligible else "needs_input"
        else:
            status = "needs_selection" if eligible else "needs_input"
    elif any(trial.attempts == 0 or trial.status == "started" for trial in checked):
        status = "running"
    elif choices:
        status = "needs_selection"
    elif any(trial.status == "needs_input" for trial in checked):
        status = "needs_input"
    else:
        status = "failed"
    return state.model_copy(update={"choices": choices, "status": status})
