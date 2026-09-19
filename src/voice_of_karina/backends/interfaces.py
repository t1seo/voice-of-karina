"""Narrow, typed boundaries for the pinned mlx-audio public APIs."""

from __future__ import annotations

from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict

if TYPE_CHECKING:
    from collections.abc import Iterable


class AudioSamples(Protocol):
    """The evaluated mono MLX array methods consumed by the WAV writer."""

    def reshape(self, size: int) -> AudioSamples: ...

    def tolist(self) -> list[float]: ...


class GenerationChunk(Protocol):
    """The public audio fields of mlx-audio GenerationResult."""

    @property
    def audio(self) -> AudioSamples: ...

    @property
    def sample_rate(self) -> int: ...

    @property
    def token_count(self) -> int: ...


class SamplingParameters(TypedDict, total=False):
    """Optional Qwen sampling arguments; omission retains legacy defaults."""

    temperature: float
    top_p: float
    top_k: int
    repetition_penalty: float


class CloneModel(Protocol):
    """Qwen3 Base's reference-conditioned generation call."""

    def generate(
        self,
        *,
        text: str,
        lang_code: str,
        ref_audio: str,
        ref_text: str,
        stream: bool,
        max_tokens: int,
        temperature: float = 0.9,
        top_p: float = 1.0,
        top_k: int = 50,
        repetition_penalty: float = 1.05,
    ) -> Iterable[GenerationChunk]: ...


class DesignModel(Protocol):
    """Qwen3 VoiceDesign's distinct language and description parameters."""

    def generate_voice_design(
        self, *, text: str, language: str, instruct: str, stream: bool, max_tokens: int
    ) -> Iterable[GenerationChunk]: ...


class QwenModel(CloneModel, DesignModel, Protocol):
    """Both public capabilities exposed by the Qwen model loader."""


class SpeechSegment(TypedDict):
    """Whisper segment fields retained at the adapter boundary."""

    start: float
    end: float
    text: str
    avg_logprob: NotRequired[float]
    no_speech_prob: NotRequired[float]


class SpeechResult(Protocol):
    """Whisper's non-streaming public result."""

    @property
    def text(self) -> str: ...

    @property
    def segments(self) -> list[SpeechSegment] | None: ...

    @property
    def language(self) -> str | None: ...


class SpeechModel(Protocol):
    """Deterministic transcription without previous-clip context leakage."""

    def generate(
        self,
        audio: str,
        *,
        language: str | None,
        temperature: float,
        condition_on_previous_text: bool,
        word_timestamps: bool,
        verbose: bool | None,
    ) -> SpeechResult: ...
