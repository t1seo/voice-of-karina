"""Confidence-aware transcription of reference clips and generated speech."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

from voice_of_karina.audio_io import crop_audio
from voice_of_karina.audio_metrics import ANALYSIS_RATE
from voice_of_karina.backends.languages import asr_language
from voice_of_karina.backends.stt import Transcript, transcribe_many
from voice_of_karina.errors import VoiceError

if TYPE_CHECKING:
    from voice_of_karina.contracts import Candidate

MAX_NO_SPEECH_PROBABILITY: Final = 0.6
MIN_AVERAGE_LOG_PROBABILITY: Final = -1.0


def uncertain_transcript(transcript: Transcript) -> bool:
    """A transcript without segment evidence must not be reported as verified speech."""
    return (
        not transcript.segments
        or any(
            part.avg_logprob is None and part.no_speech_prob is None for part in transcript.segments
        )
        or any(
            (part.avg_logprob is not None and part.avg_logprob < MIN_AVERAGE_LOG_PROBABILITY)
            or (part.no_speech_prob is not None and part.no_speech_prob > MAX_NO_SPEECH_PROBABILITY)
            for part in transcript.segments
        )
    )


def transcribe_candidates(
    candidates: tuple[Candidate, ...],
    job_dir: Path,
    *,
    language: str = "Korean",
) -> tuple[tuple[Candidate, ...], str | None]:
    """Batch only ranked clips through one ASR worker, preserving native references."""
    paths: list[Path] = []
    for candidate in candidates:
        path = job_dir / "analysis" / f"{candidate.id}-16k.wav"
        paths.append(
            crop_audio(
                Path(candidate.audio_path),
                path,
                0,
                candidate.metrics.duration_seconds,
                sample_rate=ANALYSIS_RATE,
            )
        )
    try:
        transcripts = transcribe_many(tuple(paths), language=asr_language(language))
    except VoiceError as error:
        return candidates, f"Reference transcription is unavailable: {error.message}"
    results: list[Candidate] = []
    for candidate, transcript in zip(candidates, transcripts, strict=True):
        uncertain = uncertain_transcript(transcript)
        text = transcript.text.strip() if not uncertain else None
        warnings = candidate.warnings
        if uncertain:
            warnings += ("Reference transcription is uncertain; choose a clearer recording.",)
        results.append(candidate.model_copy(update={"transcript": text, "warnings": warnings}))
    return tuple(results), None
