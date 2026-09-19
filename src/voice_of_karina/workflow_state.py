"""State transitions and accepted-artifact integrity, without model execution."""

from datetime import UTC, datetime
from pathlib import Path

from voice_of_karina.contracts import AnalysisResult, Status
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import ErrorDetail, Event, JobResult, MessageResult


def accepted(result: MessageResult) -> bool:
    """An artifact is successful only after quality validation and fingerprinting."""
    return (
        result.audio is not None
        and result.quality is not None
        and result.quality.valid
        and result.quality.decision == "pass"
        and result.accepted_sha256 is not None
    )


def transition(
    state: JobResult,
    step: str,
    detail: str,
    *,
    status: Status = "running",
    input_request: str | None = None,
) -> JobResult:
    """Append a transition while preserving all previous job evidence."""
    return state.model_copy(
        update={
            "step": step,
            "status": status,
            "input_request": input_request,
            "events": (
                *state.events,
                Event(at=datetime.now(UTC).isoformat(), step=step, detail=detail),
            ),
        }
    )


def integrity(state: JobResult, store: Store) -> JobResult:
    """Status and resume refuse a deleted, replaced, or escaped accepted artifact."""
    checked: list[MessageResult] = []
    changed = False
    for result in state.messages:
        audio = result.audio
        if not accepted(result) or audio is None:
            checked.append(result)
            continue
        path = Path(audio.path)
        valid = (
            path.resolve().is_relative_to(store.job_dir(state.job_id))
            and path.is_file()
            and file_digest(path) == result.accepted_sha256
        )
        if valid:
            checked.append(result)
            continue
        changed = True
        checked.append(
            result.model_copy(
                update={
                    "audio": None,
                    "quality": None,
                    "accepted_sha256": None,
                    "errors": (
                        *result.errors,
                        ErrorDetail(
                            code="artifact_changed", message="Accepted audio is missing or changed."
                        ),
                    ),
                }
            )
        )
    if not changed:
        return state
    status: Status = "partial" if any(accepted(result) for result in checked) else "failed"
    return state.model_copy(
        update={"messages": tuple(checked), "status": status, "step": "artifact_verification"}
    )


def has_transcripts(analysis: AnalysisResult) -> bool:
    """Selection prompts can coexist with valid transcripts; ASR failures cannot."""
    return any(
        candidate.transcript and candidate.transcript.strip() for candidate in analysis.candidates
    )
