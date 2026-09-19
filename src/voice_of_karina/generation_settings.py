"""Portable sampling recipes and evidence for the exact generated audio."""

import hashlib
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from voice_of_karina.errors import VoiceError

type Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
type AudioDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

_SEED_MODULUS: Final = 2**32
_MAX_ATTEMPTS: Final = 3


class GenerationSettings(BaseModel):
    """Only controls that the pinned Qwen Base ICL path actually applies."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    seed: int = Field(default=20260920, ge=0, lt=_SEED_MODULUS, strict=True)
    temperature: float = Field(default=0.9, gt=0, le=2, allow_inf_nan=False)
    top_p: float = Field(default=1.0, gt=0, le=1, allow_inf_nan=False)
    top_k: int = Field(default=50, ge=1, le=100, strict=True)
    repetition_penalty: float = Field(default=1.5, ge=1.5, le=2.5, allow_inf_nan=False)


class SynthesisEvidence(BaseModel):
    """Bind applied sampling and requested text to a particular WAV and runtime."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    settings: GenerationSettings
    text: Nonempty
    audio_sha256: AudioDigest
    runtime: Nonempty


class VoiceRecipe(BaseModel):
    """Reusable synthesis conditions; reference identity is stored separately."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    language: Nonempty = "Korean"
    settings: GenerationSettings
    model_id: Nonempty
    model_revision: Nonempty


def settings_for_attempt(
    settings: GenerationSettings, message_id: str, attempt: int
) -> GenerationSettings:
    """Keep the selected first seed and assign stable, distinct seeds to retries."""
    if not 1 <= attempt <= _MAX_ATTEMPTS:
        raise VoiceError("invalid_generation_attempt", "The generation attempt must be 1 to 3.")
    if attempt == 1:
        return settings
    digest = hashlib.sha256(message_id.encode("utf-8")).digest()
    stride = int.from_bytes(digest[:4], "big") | 1
    seed = (settings.seed + stride * (attempt - 1)) % _SEED_MODULUS
    return settings.model_copy(update={"seed": seed})
