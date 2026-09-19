"""Bounded per-message retries and recovery of completed model artifacts."""

from pathlib import Path

from pydantic import ValidationError

from voice_of_karina.contracts import GeneratedAudio, GenerateRequest, QualityResult, Status
from voice_of_karina.errors import VoiceError
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import Adapters, ErrorDetail, JobResult, MessageResult
from voice_of_karina.workflow_state import accepted, stale_quality, transition


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
    recovered = list(state.messages)
    for index, previous in enumerate(recovered):
        result = previous
        if stale_quality(result) and result.audio is not None:
            try:
                result = checked_output(result, result.audio, request, directory, adapters)
            except (OSError, VoiceError) as error:
                result = validation_failure(result, error)
            recovered[index] = result
            state = store.save(state.model_copy(update={"messages": tuple(recovered)}))
        if accepted(result) or result.attempts == 0:
            continue
        manifests = sorted(
            directory.glob(f"attempt-*/{result.message_id}.audio.json"), reverse=True
        )
        for manifest in manifests:
            try:
                audio = GeneratedAudio.model_validate_json(manifest.read_bytes())
                if result.audio is not None and audio.path == result.audio.path:
                    continue
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


def validate_batch(
    state: JobResult,
    request: GenerateRequest,
    store: Store,
    adapters: Adapters,
    outputs: list[GeneratedAudio],
) -> JobResult:
    """Persist each accepted message immediately so later failures cannot lose it."""
    pending_ids = {message.id for message in request.messages}
    checked = list(state.messages)
    for index, result in enumerate(checked):
        if result.message_id not in pending_ids:
            continue
        matches = [audio for audio in outputs if audio.message_id == result.message_id]
        if len(matches) != 1:
            checked[index] = result.model_copy(
                update={
                    "quality": QualityResult(
                        valid=False,
                        decision="retry",
                        warnings=("Expected exactly one matching output.",),
                    )
                }
            )
        else:
            try:
                checked[index] = checked_output(
                    result, matches[0], request, store.job_dir(state.job_id), adapters
                )
            except (VoiceError, OSError) as error:
                checked[index] = validation_failure(result, error)
        state = store.save(state.model_copy(update={"messages": tuple(checked)}))
    return state


def run_generation(
    state: JobResult, request: GenerateRequest, store: Store, adapters: Adapters
) -> JobResult:
    """Persist attempts before inference and regenerate only rejected utterances."""
    state = recover_outputs(state, request, store, adapters)
    while True:
        pending = tuple(
            result
            for result in state.messages
            if not accepted(result)
            and result.attempts < request.max_attempts
            and (result.quality is None or result.quality.decision != "needs_input")
        )
        if not pending:
            break
        pending_ids = {result.message_id for result in pending}
        incremented = tuple(
            result.model_copy(update={"attempts": result.attempts + 1})
            if result.message_id in pending_ids
            else result
            for result in state.messages
        )
        state = store.save(
            transition(
                state.model_copy(update={"messages": incremented}),
                "generating",
                "Persisted attempts before model invocation.",
            )
        )
        attempt = max(result.attempts for result in state.messages)
        directory = store.job_dir(state.job_id) / f"attempt-{attempt}"
        directory.mkdir(parents=True, exist_ok=True)
        subset = request.model_copy(
            update={
                "messages": tuple(
                    message for message in request.messages if message.id in pending_ids
                )
            }
        )
        try:
            outputs = adapters.synthesize(subset, state.reference, directory)
        except (VoiceError, OSError) as error:
            reason = ErrorDetail(code="generation_failed", message=str(error))
            state = store.save(
                state.model_copy(
                    update={
                        "messages": tuple(
                            result.model_copy(update={"errors": (*result.errors, reason)})
                            if result.message_id in pending_ids
                            else result
                            for result in state.messages
                        )
                    }
                )
            )
            state = recover_outputs(state, request, store, adapters)
            continue
        state = store.save(transition(state, "validating", "Checking each requested utterance."))
        state = validate_batch(state, subset, store, adapters, outputs)
    successes = sum(accepted(result) for result in state.messages)
    needs_input = any(
        result.quality is not None and result.quality.decision == "needs_input"
        for result in state.messages
        if not accepted(result)
    )
    status: Status
    if successes == len(state.messages):
        status = "complete"
    elif needs_input:
        status = "needs_input"
    elif successes:
        status = "partial"
    else:
        status = "failed"
    return store.save(
        transition(
            state,
            "done",
            "The bounded generation loop has finished.",
            status=status,
            input_request="Review quality warnings before making a new request."
            if needs_input
            else (
                "Generation attempts are exhausted. Review rejected audio and make a new request."
                if successes < len(state.messages)
                else None
            ),
        )
    )
