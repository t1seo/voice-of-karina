"""Persist digest-specific review rejections before the existing resume loop runs."""

from pathlib import Path

from voice_of_karina.contracts import GenerateRequest, QualityResult, RejectRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import JobResult, ReviewRejection
from voice_of_karina.workflow_state import accepted, transition


def reject_output(state: JobResult, request: RejectRequest, store: Store) -> JobResult:
    """Make replay harmless and refuse a review referring to different current bytes."""
    if not isinstance(state.request, GenerateRequest):
        raise VoiceError("invalid_review", "Only a generated message can be reviewed.")
    selected = next(
        (item for item in state.messages if item.message_id == request.message_id), None
    )
    if selected is None:
        raise VoiceError("unknown_message", "The reviewed message is not part of this job.")
    if any(item.sha256 == request.sha256 for item in selected.rejections):
        return state
    audio = selected.audio
    if audio is None:
        raise VoiceError("stale_review", "There is no current artifact matching this review.")
    path = Path(audio.path)
    if (
        not path.resolve().is_relative_to(store.job_dir(state.job_id))
        or not path.is_file()
        or file_digest(path) != request.sha256
    ):
        raise VoiceError("stale_review", "The reviewed digest does not match the current artifact.")
    rejected = selected.model_copy(
        update={
            "rejections": (
                *selected.rejections,
                ReviewRejection(
                    sha256=request.sha256,
                    reason=request.reason,
                    audio=audio,
                    quality=selected.quality,
                ),
            ),
            "quality": QualityResult(
                valid=False,
                decision="retry",
                policy_version=QUALITY_POLICY_VERSION,
                warnings=(f"Rejected after quality review: {request.reason}",),
            ),
            "accepted_sha256": None,
        }
    )
    messages = tuple(
        rejected if item.message_id == request.message_id else item for item in state.messages
    )
    remaining = state.request.max_attempts - selected.attempts
    return store.save(
        transition(
            state.model_copy(update={"messages": messages}),
            "reviewed",
            f"Rejected {request.message_id} artifact {request.sha256}: {request.reason}",
            status="needs_input"
            if remaining
            else ("partial" if any(accepted(item) for item in messages) else "failed"),
            input_request=f"Resume this job to regenerate with {remaining} remaining attempt(s)."
            if remaining
            else "No generation attempts remain; the reviewed artifact stays rejected.",
        )
    )
