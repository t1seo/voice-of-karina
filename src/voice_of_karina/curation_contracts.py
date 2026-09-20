"""Private skill operations for exact voice registration and bounded comparisons."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from voice_of_karina.contracts import (
    FrozenModel,
    Identifier,
    Message,
    QualityOptions,
    Reference,
    Sha256,
    Text,
)
from voice_of_karina.generation_settings import GenerationSettings, VoiceRecipe
from voice_of_karina.workflow_models import VoiceProfile

type Seed = Annotated[int, Field(ge=0, lt=2**32, strict=True)]


class RegisterRequest(FrozenModel):
    """Validate and copy an exact reference without applying another crop."""

    action: Literal["register"] = "register"
    name: Text
    reference: Reference
    recipe: VoiceRecipe | None = None


class PresetRequest(FrozenModel):
    """Register a versioned reference packaged with the installed engine."""

    action: Literal["preset"] = "preset"
    preset_id: Identifier
    profile_name: Text | None = None


class RegisterCandidateRequest(FrozenModel):
    """Register a confirmed analysis candidate with its recorded digest and transcript."""

    action: Literal["register_candidate"] = "register_candidate"
    analysis_job_id: Identifier
    candidate_id: Identifier
    name: Text
    recipe: VoiceRecipe | None = None


class ProfileResult(FrozenModel):
    """Return the durable profile, including its exact reference and recipe."""

    status: Literal["complete"] = "complete"
    voice: VoiceProfile


class AuditionRequest(FrozenModel):
    """Compare confirmed same-speaker references under paired sampling conditions."""

    action: Literal["audition"] = "audition"
    voice_ids: tuple[Identifier, ...] = Field(min_length=2, max_length=3)
    messages: tuple[Message, ...] = Field(min_length=1, max_length=2)
    seeds: tuple[Seed, ...] = Field(default=(20260920, 20260921), min_length=1, max_length=2)
    settings: GenerationSettings = Field(default_factory=GenerationSettings)
    language: Text = "Korean"
    quality: QualityOptions = Field(default_factory=QualityOptions)

    @model_validator(mode="after")
    def unique_trials(self) -> Self:
        """Every cell in the finite matrix must have a distinct identity."""
        if len(set(self.voice_ids)) != len(self.voice_ids):
            raise PydanticCustomError("duplicate_voice", "Choose distinct reference profiles")
        if len({message.id for message in self.messages}) != len(self.messages):
            raise PydanticCustomError("duplicate_message", "Message ids must be unique")
        if len(set(self.seeds)) != len(self.seeds):
            raise PydanticCustomError("invalid_seeds", "Use distinct uint32 seeds")
        return self


class SelectVoiceRequest(FrozenModel):
    """Promote one reviewed, hash-bound comparison choice into a reusable voice."""

    action: Literal["select_voice"] = "select_voice"
    job_id: Identifier
    choice_id: Sha256
    name: Text
    reason: Text
