"""Validate artifacts and recover completed children without spending new attempts."""

from pathlib import Path

from pydantic import ValidationError

from voice_of_karina.contracts import GeneratedAudio, GenerateRequest, QualityResult
from voice_of_karina.errors import VoiceError
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_invocation import validate_invocation_audio
from voice_of_karina.workflow_models import Adapters, ErrorDetail, JobResult, MessageResult
from voice_of_karina.workflow_state import accepted, stale_quality


def attempt_directories(directory: Path) -> list[Path]:
    """Keep persisted batches in numeric order even after multiple interruptions."""
    return sorted(
        (
            path
            for path in directory.glob("attempt-*")
            if path.is_dir() and path.name.removeprefix("attempt-").isdecimal()
        ),
        key=lambda path: int(path.name.removeprefix("attempt-")),
        reverse=True,
    )


def checked_output(
    result: MessageResult,
    audio: GeneratedAudio,
    request: GenerateRequest,
    directory: Path,
    adapters: Adapters,
) -> MessageResult:
    """Accept only contained, quality-checked files for the expected utterance."""
    path = Path(audio.path)
    if audio.message_id != result.message_id or not path.resolve().is_relative_to(directory):
        raise VoiceError("invalid_output", "The model returned an unrelated output artifact.")
    rejected_digests = {item.sha256 for item in result.rejections}
    if rejected_digests and path.is_file() and file_digest(path) in rejected_digests:
        return result.model_copy(
            update={
                "audio": audio,
                "accepted_sha256": None,
                "quality": QualityResult(
                    valid=False,
                    decision="retry",
                    policy_version=QUALITY_POLICY_VERSION,
                    warnings=("This artifact was rejected after quality review.",),
                ),
            }
        )
    quality = adapters.validate(audio, result.text, request.quality)
    if quality.decision == "pass" and quality.policy_version != QUALITY_POLICY_VERSION:
        quality = quality.model_copy(
            update={
                "valid": False,
                "decision": "needs_input",
                "warnings": (*quality.warnings, "The checker did not report the current policy."),
            }
        )
    digest = (
        file_digest(path)
        if quality.valid and quality.decision == "pass" and path.is_file()
        else None
    )
    if digest is None and quality.decision == "pass":
        quality = QualityResult(
            valid=False, decision="retry", warnings=("Output file is missing.",)
        )
    return result.model_copy(update={"audio": audio, "quality": quality, "accepted_sha256": digest})


def validation_failure(result: MessageResult, error: VoiceError | OSError) -> MessageResult:
    """Retain failed validation evidence without keeping an earlier acceptance digest."""
    return result.model_copy(
        update={
            "quality": QualityResult(valid=False, decision="retry", warnings=(str(error),)),
            "accepted_sha256": None,
            "errors": (
                *result.errors,
                ErrorDetail(code="validation_failed", message=str(error)),
            ),
        }
    )


def recover_outputs(
    state: JobResult, request: GenerateRequest, store: Store, adapters: Adapters
) -> JobResult:
    """A child may finish WAVs before an interrupted parent receives its response."""
    directory = store.job_dir(state.job_id)
    batches = attempt_directories(directory)
    recovered = list(state.messages)
    for index, previous in enumerate(recovered):
        result = previous
        if stale_quality(result) and result.audio is not None:
            try:
                validate_invocation_audio(state, result, result.audio, directory)
                result = checked_output(result, result.audio, request, directory, adapters)
            except (OSError, VoiceError) as error:
                result = validation_failure(result, error)
            recovered[index] = result
            state = store.save(state.model_copy(update={"messages": tuple(recovered)}))
        if accepted(result) or result.attempts == 0:
            continue
        manifests = (batch / f"{result.message_id}.audio.json" for batch in batches)
        for manifest in manifests:
            try:
                audio = GeneratedAudio.model_validate_json(manifest.read_bytes())
                if result.audio is not None and audio.path == result.audio.path:
                    continue
                validate_invocation_audio(state, result, audio, directory)
                checked = checked_output(result, audio, request, directory, adapters)
            except (OSError, ValidationError, VoiceError):
                continue
            if accepted(checked) or result.audio is None:
                recovered[index] = checked
                state = store.save(state.model_copy(update={"messages": tuple(recovered)}))
                result = checked
            if accepted(checked):
                break
    return state
