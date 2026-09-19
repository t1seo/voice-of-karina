"""Persisted job state and the narrow adapters used by the workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, assert_never

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from voice_of_karina.contracts import (
    AnalysisResult,
    AnalyzeRequest,
    Candidate,
    FrozenModel,
    GeneratedAudio,
    GenerateRequest,
    Identifier,
    QualityOptions,
    QualityResult,
    Reference,
    Sha256,
    Status,
    Text,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class ErrorDetail(FrozenModel):
    """An actionable failure retained even when a later attempt succeeds."""

    code: str
    message: str


class Event(FrozenModel):
    """One append-only transition in a persisted job."""

    at: str
    step: str
    detail: str


class ReviewRejection(FrozenModel):
    """Retain the artifact and automatic verdict that a later review rejected."""

    sha256: Sha256
    reason: Text
    audio: GeneratedAudio
    quality: QualityResult | None


class MessageResult(FrozenModel):
    """Attempts and verified output for exactly one requested utterance."""

    message_id: Identifier
    text: str
    attempts: int = 0
    audio: GeneratedAudio | None = None
    quality: QualityResult | None = None
    accepted_sha256: str | None = None
    rejections: tuple[ReviewRejection, ...] = Field(default=(), max_length=3)
    errors: tuple[ErrorDetail, ...] = ()


class JobResult(FrozenModel):
    """The result is also the resumable on-disk state; no hidden mutable state."""

    job_id: Identifier
    request: AnalyzeRequest | GenerateRequest = Field(discriminator="action")
    status: Status = "running"
    step: str = "created"
    input_request: str | None = None
    analysis: AnalysisResult | None = None
    reference: Reference | None = None
    messages: tuple[MessageResult, ...] = ()
    voice_id: Identifier | None = None
    selected_candidate_id: Identifier | None = None
    events: tuple[Event, ...] = ()
    errors: tuple[ErrorDetail, ...] = ()

    @model_validator(mode="after")
    def matching_messages(self) -> Self:
        """A damaged state file cannot drop requested utterances or reset their identity."""
        match self.request:
            case GenerateRequest() as request:
                expected = tuple((message.id, message.text) for message in request.messages)
                recorded = tuple((message.message_id, message.text) for message in self.messages)
                if recorded != expected:
                    raise PydanticCustomError(
                        "saved_messages", "Saved messages differ from the original request"
                    )
                if any(
                    message.attempts > request.max_attempts or message.attempts < 0
                    for message in self.messages
                ):
                    raise PydanticCustomError(
                        "saved_attempts", "Saved attempts exceed the request budget"
                    )
            case AnalyzeRequest():
                if self.messages:
                    raise PydanticCustomError(
                        "saved_messages", "An analysis job cannot contain generated messages"
                    )
            case unreachable:
                assert_never(unreachable)
        return self


class VoiceProfile(FrozenModel):
    """A reusable voice owns its copied reference and exact transcript."""

    id: Identifier
    name: str
    reference: Reference
    created_at: str


class VoicesResult(FrozenModel):
    """Voice profiles available to the conversational skill."""

    status: str = "complete"
    voices: tuple[VoiceProfile, ...]


@dataclass(frozen=True, slots=True)
class Adapters:
    """Inject side-effect boundaries without shipping a fake model backend."""

    analyze: Callable[[AnalyzeRequest | GenerateRequest, Path], AnalysisResult]
    prepare: Callable[[Candidate, Path], Reference]
    design: Callable[[GenerateRequest, Path], Reference]
    synthesize: Callable[[GenerateRequest, Reference | None, Path], list[GeneratedAudio]]
    validate: Callable[[GeneratedAudio, str, QualityOptions], QualityResult]
