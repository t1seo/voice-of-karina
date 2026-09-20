"""Reference creation and synthesis entry points used by the durable workflow."""

import hashlib
from pathlib import Path
from typing import Final, assert_never

from pydantic import ValidationError

from voice_of_karina.contracts import GeneratedAudio, GenerateRequest, Provenance, Reference
from voice_of_karina.errors import VoiceError

from .client import run_task
from .languages import asr_language, tts_language
from .models import CLONE_MODEL, DESIGN_MODEL, DESIGN_SEEDS
from .protocol import AudioResponse, CloneTask, DesignTask, TranscriptResponse
from .stt import transcribe_audio

_MAX_NO_SPEECH_PROBABILITY: Final = 0.6
_MIN_AVERAGE_LOG_PROBABILITY: Final = -1.0


def validate_reference(reference: Reference) -> Path:
    """Refuse missing or modified voice data before allocating a model."""
    path = Path(reference.audio_path).resolve()
    if not path.is_file():
        raise VoiceError("missing_reference", f"The saved voice reference is missing: {path}")
    if not reference.transcript.strip():
        raise VoiceError("missing_transcript", "The reference needs an exact spoken transcript.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != reference.sha256:
        raise VoiceError(
            "changed_reference", "The saved voice reference changed. Analyze it again."
        )
    return path


def design_reference(request: GenerateRequest, job_dir: Path) -> Reference:
    """Create, transcribe, and persist one stable voice for generation and retries."""
    if not request.voice_description:
        raise VoiceError(
            "missing_voice_description", "Describe the original voice you want to create."
        )
    destination = job_dir.resolve() / "designed-reference.wav"
    manifest = destination.with_suffix(".json")
    if manifest.is_file():
        try:
            saved = Reference.model_validate_json(manifest.read_text(encoding="utf-8"))
        except ValidationError as error:
            raise VoiceError(
                "invalid_reference", "The saved design reference record is invalid."
            ) from error
        if (
            saved.provenance.description != request.voice_description
            or saved.model_id != DESIGN_MODEL.repository
            or saved.model_revision != DESIGN_MODEL.revision
            or Path(saved.audio_path).resolve() != destination
        ):
            raise VoiceError(
                "reference_conflict", "This directory contains a different designed voice."
            )
        _ = validate_reference(saved)
        return saved
    language = tts_language(request.language)
    seed = DESIGN_SEEDS.get(language.lower(), request.messages[0].text)
    response = run_task(
        DesignTask(
            text=seed,
            description=request.voice_description,
            language=language,
            output_path=destination,
        )
    )
    match response:
        case AudioResponse():
            if len(response.audio) != 1 or Path(response.audio[0].path).resolve() != destination:
                raise VoiceError(
                    "invalid_model_response", "VoiceDesign did not return its reference."
                )
        case TranscriptResponse():
            raise VoiceError(
                "invalid_model_response", "VoiceDesign returned text instead of audio."
            )
        case _:
            assert_never(response)
    transcript = transcribe_audio(destination, language=asr_language(language))
    if (
        not transcript.text.strip()
        or not transcript.segments
        or any(
            (segment.no_speech_prob is None and segment.avg_logprob is None)
            or (
                segment.no_speech_prob is not None
                and segment.no_speech_prob > _MAX_NO_SPEECH_PROBABILITY
            )
            or (
                segment.avg_logprob is not None
                and segment.avg_logprob < _MIN_AVERAGE_LOG_PROBABILITY
            )
            for segment in transcript.segments
        )
    ):
        raise VoiceError(
            "uncertain_reference_transcript",
            "The reference transcript is uncertain. Refine the voice description and retry.",
        )
    reference = Reference(
        audio_path=str(destination),
        sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
        transcript=transcript.text.strip(),
        provenance=Provenance(kind="design", description=request.voice_description),
        preparation=("original", "asr_transcribed"),
        model_id=DESIGN_MODEL.repository,
        model_revision=DESIGN_MODEL.revision,
    )
    pending = manifest.with_suffix(".json.pending")
    _ = pending.write_text(reference.model_dump_json(), encoding="utf-8")
    _ = pending.replace(manifest)
    return reference


def _validated_audio(task: CloneTask, response: AudioResponse) -> list[GeneratedAudio]:
    """Accept only output files and evidence matching this exact worker request."""
    if tuple(item.message_id for item in response.audio) != tuple(
        message.id for message in task.messages
    ):
        raise VoiceError(
            "invalid_model_response", "The model returned an incomplete message batch."
        )
    for message, item in zip(task.messages, response.audio, strict=True):
        expected = task.output_dir / f"{item.message_id}.wav"
        if (
            Path(item.path).resolve() != expected
            or not expected.is_file()
            or item.sample_rate <= 0
            or item.duration_seconds <= 0
        ):
            raise VoiceError(
                "invalid_model_response", "The model returned a missing or invalid WAV."
            )
        if task.settings is not None:
            evidence = item.synthesis
            if (
                evidence is None
                or evidence.settings != task.settings
                or evidence.text != message.text
                or evidence.audio_sha256 != hashlib.sha256(expected.read_bytes()).hexdigest()
                or item.reference != task.reference
                or item.model_id != CLONE_MODEL.repository
                or item.model_revision != CLONE_MODEL.revision
                or item.language != task.language
            ):
                raise VoiceError(
                    "invalid_model_response",
                    "The synthesis evidence differs from the requested voice or settings.",
                )
    return list(response.audio)


def synthesize(
    request: GenerateRequest, reference: Reference | None, output_dir: Path
) -> list[GeneratedAudio]:
    """Generate only requested lines with one Base load and an immutable reference."""
    selected: Reference
    if reference is None:
        match request.mode:
            case "design":
                selected = design_reference(request, output_dir / "reference")
            case "mimic" | "reuse":
                raise VoiceError("missing_reference", "Select or prepare a voice reference first.")
            case _:
                assert_never(request.mode)
    else:
        selected = reference
    _ = validate_reference(selected)
    destination = output_dir.resolve()
    task = CloneTask(
        messages=request.messages,
        reference=selected,
        output_dir=destination,
        language=tts_language(request.language),
        settings=request.settings,
    )
    response = run_task(task)
    match response:
        case AudioResponse():
            return _validated_audio(task, response)
        case TranscriptResponse():
            raise VoiceError(
                "invalid_model_response", "The synthesis worker returned text instead of WAVs."
            )
        case _:
            assert_never(response)
