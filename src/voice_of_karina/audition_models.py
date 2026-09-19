"""Finite comparison state, individual consumed trials, and hash-bound choices."""

import hashlib
from itertools import product
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from voice_of_karina.contracts import (
    FrozenModel,
    GeneratedAudio,
    Identifier,
    Message,
    QualityResult,
    Sha256,
    Text,
)
from voice_of_karina.curation_contracts import AuditionRequest
from voice_of_karina.generation_settings import GenerationSettings, VoiceRecipe
from voice_of_karina.workflow_models import ErrorDetail, VoiceProfile, VoiceSelection


class AuditionTrial(FrozenModel):
    """One matrix cell owns at most one synthesis invocation."""

    id: Identifier
    voice_id: Identifier
    message: Message
    settings: GenerationSettings
    attempts: Literal[0, 1] = 0
    status: Literal["pending", "started", "passed", "failed", "needs_input"] = "pending"
    audio: GeneratedAudio | None = None
    quality: QualityResult | None = None
    errors: tuple[ErrorDetail, ...] = ()

    def has_pass_evidence(self) -> bool:
        """Require complete pass evidence while allowing old policy versions to load."""
        return (
            self.attempts == 1
            and self.audio is not None
            and self.audio.synthesis is not None
            and self.quality is not None
            and self.quality.valid
            and self.quality.decision == "pass"
        )


class AuditionChoice(FrozenModel):
    """Eligible previews share one reference and seed across all requested texts."""

    choice_id: Sha256
    voice_id: Identifier
    recipe: VoiceRecipe
    trial_ids: tuple[Identifier, ...]
    audio_sha256: tuple[Sha256, ...]


def trial_matrix(request: AuditionRequest) -> tuple[AuditionTrial, ...]:
    """Derive every cell from the original bounded request without replenishment."""
    trials: list[AuditionTrial] = []
    for voice_id, seed, message in product(request.voice_ids, request.seeds, request.messages):
        identity = f"{voice_id}\n{seed}\n{message.model_dump_json()}"
        trials.append(
            AuditionTrial(
                id="trial-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                voice_id=voice_id,
                message=message,
                settings=request.settings.model_copy(update={"seed": seed}),
            )
        )
    return tuple(trials)


class AuditionResult(FrozenModel):
    """The response is the durable study ledger, including failures and selection."""

    version: Literal[1] = 1
    job_id: Identifier
    request: AuditionRequest
    profiles: tuple[VoiceProfile, ...]
    trials: tuple[AuditionTrial, ...] = Field(min_length=2, max_length=12)
    choices: tuple[AuditionChoice, ...] = ()
    status: Literal["running", "needs_selection", "needs_input", "failed", "complete"] = "running"
    selection: VoiceSelection | None = None
    selection_name: Text | None = None
    selected_voice_id: Identifier | None = None

    @model_validator(mode="after")
    def preserve_matrix(self) -> Self:
        """Persisted state cannot drop cells, change their settings, or invent attempts."""
        expected = trial_matrix(self.request)
        if tuple((t.id, t.voice_id, t.message, t.settings) for t in self.trials) != tuple(
            (t.id, t.voice_id, t.message, t.settings) for t in expected
        ):
            raise PydanticCustomError("invalid_matrix", "Saved trials differ from the request")
        if tuple(profile.id for profile in self.profiles) != self.request.voice_ids:
            raise PydanticCustomError(
                "invalid_profiles", "Saved references differ from the request"
            )
        if any(
            (trial.attempts == 0) != (trial.status == "pending")
            or (trial.attempts == 0 and (trial.audio is not None or trial.quality is not None))
            for trial in self.trials
        ):
            raise PydanticCustomError("invalid_attempts", "Saved trial budget is inconsistent")
        if any(trial.status == "passed" and not trial.has_pass_evidence() for trial in self.trials):
            raise PydanticCustomError(
                "invalid_pass_evidence",
                "Passed trials require audio, synthesis, and valid pass quality",
            )
        if (self.selection is None) != (self.selection_name is None) or (
            self.selected_voice_id is not None and self.selection is None
        ):
            raise PydanticCustomError("invalid_selection", "Saved selection evidence is incomplete")
        if self.selection is not None and self.selection.audition_id != self.job_id:
            raise PydanticCustomError("invalid_selection", "Selection belongs to another audition")
        return self
