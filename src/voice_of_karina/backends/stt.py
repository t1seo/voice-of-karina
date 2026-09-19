"""Public transcription helpers that release Whisper memory before returning."""

from pathlib import Path
from typing import assert_never

from voice_of_karina.errors import VoiceError

from .client import run_task
from .protocol import (
    AudioResponse,
    TranscribeTask,
    Transcript,
    TranscriptResponse,
    TranscriptSegment,
)

__all__ = ["Transcript", "TranscriptSegment", "transcribe", "transcribe_audio", "transcribe_many"]


def transcribe_many(
    paths: tuple[Path, ...], *, language: str | None = "ko"
) -> tuple[Transcript, ...]:
    """Preserve clip order while loading Whisper only once per nonempty batch."""
    if not paths:
        return ()
    for path in paths:
        if not path.is_file():
            raise VoiceError("missing_audio", f"Cannot transcribe a missing file: {path}")
    response = run_task(
        TranscribeTask(paths=tuple(path.resolve() for path in paths), language=language)
    )
    match response:
        case TranscriptResponse():
            if len(response.transcripts) != len(paths):
                raise VoiceError(
                    "invalid_model_response", "Whisper did not return one result per clip."
                )
            return response.transcripts
        case AudioResponse():
            raise VoiceError(
                "invalid_model_response", "Whisper returned audio instead of a transcript."
            )
        case _:
            assert_never(response)


def transcribe_audio(path: Path, *, language: str | None = "ko") -> Transcript:
    """Return spoken text together with timestamp and uncertainty evidence."""
    return transcribe_many((path,), language=language)[0]


def transcribe(path: Path, *, language: str | None = "ko") -> str:
    """Return the measured transcript without a persistent model allocation."""
    return transcribe_audio(path, language=language).text
