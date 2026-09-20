"""Validated messages exchanged with the isolated model worker."""

from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field, TypeAdapter

from voice_of_karina.contracts import FrozenModel, GeneratedAudio, Message, Reference
from voice_of_karina.generation_settings import GenerationSettings


class CloneTask(FrozenModel):
    """One model load followed by all requested utterances."""

    operation: Literal["clone"] = "clone"
    messages: tuple[Message, ...] = Field(min_length=1)
    reference: Reference
    output_dir: Path
    language: str
    settings: GenerationSettings | None = None

    @property
    def timeout_seconds(self) -> int:
        """Include loading time plus a bounded per-message inference budget."""
        return min(7200, 600 + 300 * len(self.messages))


class DesignTask(FrozenModel):
    """Create one reusable reference before the clone stage."""

    operation: Literal["design"] = "design"
    text: str = Field(min_length=1)
    description: str = Field(min_length=1)
    language: str
    output_path: Path

    @property
    def timeout_seconds(self) -> int:
        """Bound the one-time reference design stage."""
        return 1200


class TranscribeTask(FrozenModel):
    """A bounded set of independent clips transcribed with one model."""

    operation: Literal["transcribe"] = "transcribe"
    paths: tuple[Path, ...] = Field(min_length=1, max_length=64)
    language: str | None = "ko"

    @property
    def timeout_seconds(self) -> int:
        """Allow model loading while keeping a finite batch deadline."""
        return min(3600, 600 + 120 * len(self.paths))


class TranscriptSegment(FrozenModel):
    """Timestamp and uncertainty evidence from an actual ASR segment."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str
    avg_logprob: float | None = Field(default=None, allow_inf_nan=False)
    no_speech_prob: float | None = Field(default=None, ge=0, le=1)


class Transcript(FrozenModel):
    """An exact clip transcript, including available confidence evidence."""

    text: str
    segments: tuple[TranscriptSegment, ...] = ()
    language: str = ""


class AudioResponse(FrozenModel):
    """Files created by a completed synthesis stage."""

    outcome: Literal["audio"] = "audio"
    audio: tuple[GeneratedAudio, ...]


class TranscriptResponse(FrozenModel):
    """Transcripts in exactly the same order as the input clips."""

    outcome: Literal["transcripts"] = "transcripts"
    transcripts: tuple[Transcript, ...]


class FailureResponse(FrozenModel):
    """An actionable worker failure rather than a successful empty result."""

    outcome: Literal["failure"] = "failure"
    code: str
    message: str


type WorkerTask = Annotated[
    CloneTask | DesignTask | TranscribeTask, Field(discriminator="operation")
]
type WorkerResponse = Annotated[
    AudioResponse | TranscriptResponse | FailureResponse, Field(discriminator="outcome")
]
TASK_ADAPTER: TypeAdapter[WorkerTask] = TypeAdapter(WorkerTask)
RESPONSE_ADAPTER: TypeAdapter[WorkerResponse] = TypeAdapter(WorkerResponse)
