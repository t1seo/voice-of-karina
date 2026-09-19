"""Durable identities for consumed recipe attempts and their recoverable artifacts."""

from pathlib import Path

from pydantic import Field, ValidationError

from voice_of_karina.contracts import (
    FrozenModel,
    GeneratedAudio,
    GenerateRequest,
    Identifier,
    Message,
    Sha256,
    Text,
)
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import VoiceRecipe, settings_for_attempt
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import ErrorDetail, JobResult, MessageResult
from voice_of_karina.workflow_state import accepted


class GenerationInvocation(FrozenModel):
    """One consumed attempt binds a message and reference to exact applied settings."""

    job_id: Identifier
    batch_id: Identifier
    attempt: int = Field(ge=1, le=3)
    message: Message
    recipe: VoiceRecipe
    reference_sha256: Sha256
    reference_transcript: Text


def invocation_for(
    state: JobResult, result: MessageResult, attempt: int, batch_id: str
) -> GenerationInvocation:
    """Derive a record only from a persisted recipe and already consumed attempt."""
    if state.recipe is None or state.reference is None or not 1 <= attempt <= result.attempts:
        raise VoiceError("invalid_invocation", "No consumed recipe attempt matches this output.")
    settings = settings_for_attempt(state.recipe.settings, result.message_id, attempt)
    return GenerationInvocation(
        job_id=state.job_id,
        batch_id=batch_id,
        attempt=attempt,
        message=Message(id=result.message_id, text=result.text),
        recipe=state.recipe.model_copy(update={"settings": settings}),
        reference_sha256=state.reference.sha256,
        reference_transcript=state.reference.transcript,
    )


def record_invocation(
    state: JobResult, request: GenerateRequest, directory: Path, store: Store
) -> GenerateRequest:
    """Persist the invocation before a child can produce a recoverable manifest."""
    if state.recipe is None:
        return request
    if len(request.messages) != 1:
        raise VoiceError("invalid_invocation", "A recipe invocation requires one message.")
    result = next(item for item in state.messages if item.message_id == request.messages[0].id)
    invocation = invocation_for(state, result, result.attempts, directory.name)
    store.atomic_write(directory / "invocation.json", invocation)
    return request.model_copy(
        update={"language": invocation.recipe.language, "settings": invocation.recipe.settings}
    )


def validate_invocation_audio(
    state: JobResult, result: MessageResult, audio: GeneratedAudio, directory: Path
) -> None:
    """Require both a recorded consumed invocation and matching artifact evidence."""
    if state.recipe is None:
        return
    path = Path(audio.path).resolve()
    if (
        path.parent.parent != directory.resolve()
        or not path.parent.name.removeprefix("attempt-").isdecimal()
        or not path.parent.name.startswith("attempt-")
        or path.name != f"{result.message_id}.wav"
    ):
        raise VoiceError("invalid_output", "Recipe audio is outside its recorded attempt.")
    record_path = path.with_name("invocation.json")
    if record_path.is_symlink():
        raise VoiceError("invalid_invocation", "The invocation record cannot be a link.")
    try:
        invocation = GenerationInvocation.model_validate_json(record_path.read_bytes())
    except (OSError, ValidationError) as error:
        raise VoiceError(
            "invalid_invocation", "The invocation record is missing or damaged."
        ) from error
    expected = invocation_for(state, result, invocation.attempt, path.parent.name)
    evidence = audio.synthesis
    reference = audio.reference
    if (
        invocation != expected
        or audio.message_id != result.message_id
        or audio.model_id != expected.recipe.model_id
        or audio.model_revision != expected.recipe.model_revision
        or audio.language != expected.recipe.language
        or reference is None
        or reference.sha256 != expected.reference_sha256
        or reference.transcript != expected.reference_transcript
        or evidence is None
        or evidence.settings != expected.recipe.settings
        or evidence.text != expected.message.text
        or not path.is_file()
        or evidence.audio_sha256 != file_digest(path)
    ):
        raise VoiceError("invalid_synthesis_evidence", "Audio does not match its saved invocation.")


def recipe_integrity(state: JobResult, store: Store) -> JobResult:
    """Check accepted recipe metadata without inference or writes during status reads."""
    if state.recipe is None:
        return state
    checked: list[MessageResult] = []
    changed = False
    for result in state.messages:
        if result.audio is None or result.accepted_sha256 is None:
            checked.append(result)
            continue
        try:
            validate_invocation_audio(state, result, result.audio, store.job_dir(state.job_id))
        except (VoiceError, OSError) as error:
            changed = True
            checked.append(
                result.model_copy(
                    update={
                        "audio": None,
                        "quality": None,
                        "accepted_sha256": None,
                        "errors": (
                            *result.errors,
                            ErrorDetail(code="artifact_identity_changed", message=str(error)),
                        ),
                    }
                )
            )
        else:
            checked.append(result)
    if not changed:
        return state
    return state.model_copy(
        update={
            "messages": tuple(checked),
            "status": "partial" if any(accepted(item) for item in checked) else "failed",
            "step": "artifact_verification",
        }
    )
