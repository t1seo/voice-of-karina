"""Bounded per-message retries preserve the original request and accepted siblings."""

from voice_of_karina.contracts import GeneratedAudio, GenerateRequest, QualityResult, Status
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store
from voice_of_karina.workflow_invocation import record_invocation, validate_invocation_audio
from voice_of_karina.workflow_models import Adapters, ErrorDetail, JobResult
from voice_of_karina.workflow_recovery import (
    attempt_directories,
    checked_output,
    recover_outputs,
    validation_failure,
)
from voice_of_karina.workflow_state import accepted, transition


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
                validate_invocation_audio(state, result, matches[0], store.job_dir(state.job_id))
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
        if state.recipe is not None:
            pending = pending[:1]
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
        batches = attempt_directories(store.job_dir(state.job_id))
        attempt = int(batches[0].name.removeprefix("attempt-")) + 1 if batches else 1
        directory = store.job_dir(state.job_id) / f"attempt-{attempt}"
        directory.mkdir()
        subset = request.model_copy(
            update={
                "messages": tuple(
                    message for message in request.messages if message.id in pending_ids
                )
            }
        )
        subset = record_invocation(state, subset, directory, store)
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
            input_request="Review quality warnings and revise the reference or request."
            if needs_input
            else (
                "Attempts exhausted. Revise the reference or request; do not reset the budget."
                if successes < len(state.messages)
                else None
            ),
        )
    )
