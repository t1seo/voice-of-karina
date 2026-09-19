"""Pinned local models used by the production backend."""

import hashlib
from dataclasses import dataclass
from typing import Final

from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A model identity that cannot drift between saved jobs."""

    repository: str
    revision: str


CLONE_MODEL: Final = ModelSpec(
    "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit",
    "37e955a1deb861c088ae5f3a67043185f3d1a60c",
)
DESIGN_MODEL: Final = ModelSpec(
    "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit",
    "5c390979e4b93af5f2932f90742ca99c7dd04687",
)
STT_MODEL: Final = ModelSpec(
    "mlx-community/whisper-large-v3-turbo-asr-fp16",
    "624c19c9af5603fa73b83bce14d4aeea96156d18",
)

DESIGN_SEEDS: Final = {
    "korean": (
        "안녕하세요. 편안하게 들으실 수 있도록 또렷하고 자연스럽게 말씀드릴게요. "
        "오늘도 하시는 일이 잘 풀리길 바라요. 잠깐 쉬어 가셔도 괜찮아요."
    ),
    "english": (
        "Hello. I will speak clearly and naturally so you can listen comfortably. "
        "I hope everything goes well for you today. It is okay to take a short break."
    ),
}
MAX_GENERATION_TOKENS: Final = 1536
MIN_GENERATION_TOKENS: Final = 256
TOKENS_PER_CHARACTER: Final = 6


def token_limit(text: str) -> int:
    """Bound runaway generation while accommodating the requested text length."""
    return min(MAX_GENERATION_TOKENS, max(MIN_GENERATION_TOKENS, len(text) * TOKENS_PER_CHARACTER))


def backend_cache_key() -> str:
    """Fingerprint the pinned models and generation policy without loading them."""
    parts = [
        "mlx-audio==0.5.4",
        "pcm-s16le-v1",
        "generation-budget-completion-v2",
        QUALITY_POLICY_VERSION,
        "whisper-temperature=0.0",
        "design-asr-reference-v1",
        "language-mapping-v1",
        str(MAX_GENERATION_TOKENS),
        str(MIN_GENERATION_TOKENS),
        str(TOKENS_PER_CHARACTER),
        *[f"{spec.repository}@{spec.revision}" for spec in (CLONE_MODEL, DESIGN_MODEL, STT_MODEL)],
        *[f"{language}:{seed}" for language, seed in sorted(DESIGN_SEEDS.items())],
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
