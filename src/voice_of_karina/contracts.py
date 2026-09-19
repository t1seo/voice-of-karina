"""Frozen, validated transport contracts shared by every engine adapter."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator
from pydantic_core import PydanticCustomError

type Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")]
type Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
type Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
type Mode = Literal["mimic", "design", "reuse"]
type Status = Literal["running", "needs_input", "needs_selection", "complete", "partial", "failed"]


class FrozenModel(BaseModel):
    """Reject unknown fields and accidental mutations at every JSON boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Message(FrozenModel):
    """One requested utterance; the identifier also names its output."""

    id: Identifier
    text: Text


class SourceTime(FrozenModel):
    """An explicit target utterance in one supplied source."""

    source_index: int = Field(default=0, ge=0)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        """Require a nonempty crop."""
        if self.end_seconds <= self.start_seconds:
            raise PydanticCustomError("invalid_interval", "end_seconds must exceed start_seconds")
        return self


class QualityOptions(FrozenModel):
    """Bounded quality thresholds retained as part of a reproducible request."""

    min_duration_seconds: float = Field(default=0.25, ge=0.1, le=10, allow_inf_nan=False)
    max_duration_seconds: float = Field(default=120, ge=1, le=300, allow_inf_nan=False)
    max_clipping_ratio: float = Field(default=0.02, ge=0, le=0.1, allow_inf_nan=False)
    min_rms_dbfs: float = Field(default=-50, ge=-60, le=-10, allow_inf_nan=False)
    max_text_error_rate: float = Field(default=0.15, ge=0, le=0.5, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        """Reject contradictory minimum and maximum durations."""
        if self.max_duration_seconds < self.min_duration_seconds:
            raise PydanticCustomError("invalid_quality", "Maximum duration is below minimum")
        return self


class AnalyzeRequest(FrozenModel):
    """Acquire and score candidate speech without generating any messages."""

    action: Literal["analyze"] = "analyze"
    mode: Literal["mimic"] = "mimic"
    sources: tuple[Text, ...] = Field(default=(), max_length=8)
    source_time: SourceTime | None = None
    language: Text = "Korean"

    @model_validator(mode="after")
    def valid_source_time(self) -> Self:
        """Reject a timestamp referring to a source that was not supplied."""
        if self.source_time is not None and self.source_time.source_index >= len(self.sources):
            raise PydanticCustomError("invalid_source_index", "source_index is outside sources")
        return self


class GenerateRequest(FrozenModel):
    """Generate exactly these lines from a selected or designed voice."""

    action: Literal["generate"] = "generate"
    mode: Mode
    messages: tuple[Message, ...] = Field(min_length=1, max_length=32)
    sources: tuple[Text, ...] = Field(default=(), max_length=8)
    source_time: SourceTime | None = None
    candidate_id: Identifier | None = None
    analysis_job_id: Identifier | None = None
    voice_description: Text | None = None
    voice_id: Identifier | None = None
    profile_name: Text | None = None
    language: Text = "Korean"
    max_attempts: int = Field(default=2, ge=1, le=3)
    quality: QualityOptions = Field(default_factory=QualityOptions)

    @model_validator(mode="after")
    def unique_messages(self) -> Self:
        """Avoid two utterances competing for one output path."""
        if len({message.id for message in self.messages}) != len(self.messages):
            raise PydanticCustomError("duplicate_message", "message ids must be unique")
        if self.source_time is not None and self.source_time.source_index >= len(self.sources):
            raise PydanticCustomError("invalid_source_index", "source_index is outside sources")
        if self.source_time is not None and self.candidate_id is not None:
            raise PydanticCustomError("conflicting_selection", "Choose a candidate or timestamps")
        mimic_fields = bool(
            self.sources or self.source_time or self.candidate_id or self.analysis_job_id
        )
        match self.mode:
            case "mimic":
                if self.voice_id or self.voice_description:
                    raise PydanticCustomError("conflicting_mode", "Mimic accepts recordings only")
            case "design":
                if mimic_fields or self.voice_id:
                    raise PydanticCustomError(
                        "conflicting_mode", "Design accepts a description only"
                    )
            case "reuse":
                if mimic_fields or self.voice_description:
                    raise PydanticCustomError("conflicting_mode", "Reuse accepts a voice_id only")
            case unreachable:
                assert_never(unreachable)
        return self


class StatusRequest(FrozenModel):
    """Read the persisted result without running an inference."""

    action: Literal["status"] = "status"
    job_id: Identifier


class ResumeRequest(FrozenModel):
    """Continue a saved job without replacing its original request."""

    action: Literal["resume"] = "resume"
    job_id: Identifier
    candidate_id: Identifier | None = None


class VoicesRequest(FrozenModel):
    """List reusable voice profiles."""

    action: Literal["voices"] = "voices"


class RejectRequest(FrozenModel):
    """Reject one reviewed artifact by digest without granting more attempts."""

    action: Literal["reject"] = "reject"
    job_id: Identifier
    message_id: Identifier
    sha256: Sha256
    reason: Text


class InstallRequest(FrozenModel):
    """Apply a completed artifact only when the user requests notifications."""

    action: Literal["install"] = "install"
    job_id: Identifier
    message_id: Identifier
    target: Literal["claude", "codex", "both"] = "both"


type Request = Annotated[
    AnalyzeRequest
    | GenerateRequest
    | StatusRequest
    | ResumeRequest
    | RejectRequest
    | VoicesRequest
    | InstallRequest,
    Field(discriminator="action"),
]
REQUEST_ADAPTER: TypeAdapter[Request] = TypeAdapter(Request)


class Metrics(FrozenModel):
    """Measured signal properties, not a speaker-identity or music detector."""

    duration_seconds: float = 0
    rms_dbfs: float | None = None
    peak: float | None = None
    clipping_ratio: float | None = None
    speech_ratio: float | None = None
    spectral_flatness: float | None = None
    noise_floor_dbfs: float | None = None
    text_error_rate: float | None = None
    terminal_decay_seconds: float | None = None
    terminal_drop_db: float | None = None
    ending_text_match: bool | None = None


class Source(FrozenModel):
    """A acquired recording identified by its original input and digest."""

    id: Identifier
    original: str
    audio_path: str
    sha256: str
    duration_seconds: float
    sample_rate: int = 0
    offset_seconds: float = 0


class Candidate(FrozenModel):
    """A traceable short utterance that a user can listen to and select."""

    id: Identifier
    source_id: Identifier
    audio_path: str
    start_seconds: float
    end_seconds: float
    transcript: str | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    warnings: tuple[str, ...] = ()
    needs_confirmation: bool = True
    source: str | None = None
    pause_bounded: bool = False
    utterance_count: int | None = Field(default=None, ge=1)


class AnalysisResult(FrozenModel):
    """Ranked candidates and honest uncertainty about their suitability."""

    sources: tuple[Source, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    recommended_candidate_id: Identifier | None = None
    warnings: tuple[str, ...] = ()
    input_request: str | None = None


class Provenance(FrozenModel):
    """The exact source crop or description from which a voice was created."""

    kind: Literal["mimic", "design"]
    source: str | None = None
    source_id: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    description: str | None = None


class Reference(FrozenModel):
    """Stable reference audio and matching transcript for reproducible reuse."""

    audio_path: str
    sha256: str
    transcript: str
    provenance: Provenance
    preparation: tuple[str, ...] = ("original",)
    model_id: str | None = None
    model_revision: str | None = None


class GeneratedAudio(FrozenModel):
    """One model artifact; file validation belongs to the quality adapter."""

    message_id: Identifier
    path: str
    sample_rate: int
    duration_seconds: float
    model_id: str
    model_revision: str | None = None
    elapsed_seconds: float
    language: Text = "Korean"
    reference: Reference | None = None


class QualityResult(FrozenModel):
    """Separate audio validity, text confidence, and quality warnings."""

    valid: bool
    decision: Literal["pass", "retry", "needs_input"]
    metrics: Metrics = Field(default_factory=Metrics)
    warnings: tuple[str, ...] = ()
    transcript: str | None = None
    policy_version: str | None = None
