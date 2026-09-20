"""Confidence-aware transcription of reference clips and generated speech."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

from voice_of_karina.audio_candidates import MAX_CANDIDATES
from voice_of_karina.audio_io import crop_audio
from voice_of_karina.audio_metrics import ANALYSIS_RATE, reference_score
from voice_of_karina.audio_reference_windows import (
    normalized_reference_text,
    pause_bounded_candidates,
    valid_segment_order,
)
from voice_of_karina.backends.languages import asr_language
from voice_of_karina.backends.stt import Transcript, transcribe_many
from voice_of_karina.errors import VoiceError
from voice_of_karina.sources import file_sha256

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


def _bind_digest(candidate: Candidate) -> Candidate:
    try:
        digest = file_sha256(Path(candidate.audio_path))
    except OSError as error:
        raise VoiceError(
            "changed_candidate", "The candidate audio is no longer readable."
        ) from error
    if candidate.sha256 is not None and candidate.sha256 != digest:
        raise VoiceError("changed_candidate", "The candidate audio changed during transcription.")
    return candidate.model_copy(update={"sha256": digest})


def _transcribe_native_candidates(
    candidates: tuple[Candidate, ...],
    job_dir: Path,
    *,
    language: str,
) -> tuple[Transcript, ...]:
    paths: list[Path] = []
    for candidate in candidates:
        _ = _bind_digest(candidate)
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
    transcripts = transcribe_many(tuple(paths), language=asr_language(language))
    for candidate in candidates:
        _ = _bind_digest(candidate)
    return transcripts


def transcribe_candidates(
    candidates: tuple[Candidate, ...],
    job_dir: Path,
    *,
    language: str = "Korean",
) -> tuple[tuple[Candidate, ...], str | None]:
    """Verify a bounded batch of pause crops while retaining original references."""
    try:
        bound = tuple(_bind_digest(candidate) for candidate in candidates)
        transcripts = _transcribe_native_candidates(bound, job_dir, language=language)
    except VoiceError as error:
        return candidates, f"Reference transcription is unavailable: {error.message}"
    results: list[Candidate] = []
    for candidate, transcript in zip(bound, transcripts, strict=True):
        uncertain = uncertain_transcript(transcript)
        text = transcript.text.strip() if not uncertain else None
        warnings = candidate.warnings
        if uncertain:
            warnings += ("Reference transcription is uncertain; choose a clearer recording.",)
        results.append(
            candidate.model_copy(
                update={
                    "transcript": text,
                    "warnings": warnings,
                    "utterance_count": len(transcript.segments) if text else None,
                }
            )
        )
    proposals: list[Candidate] = []
    try:
        for candidate, transcript in zip(results, transcripts, strict=True):
            if candidate.transcript:
                proposals.extend(
                    pause_bounded_candidates(
                        candidate,
                        transcript.segments,
                        job_dir,
                        limit=MAX_CANDIDATES - len(proposals),
                    )
                )
        if not proposals:
            return tuple(results), None
        bound_proposals = tuple(_bind_digest(candidate) for candidate in proposals)
        confirmations = _transcribe_native_candidates(bound_proposals, job_dir, language=language)
    except VoiceError as error:
        return tuple(results), f"Shorter reference verification is unavailable: {error.message}"
    verified = [
        candidate.model_copy(update={"transcript": transcript.text.strip()})
        for candidate, transcript in zip(bound_proposals, confirmations, strict=True)
        if not uncertain_transcript(transcript)
        and valid_segment_order(transcript.segments, candidate.metrics.duration_seconds)
        and normalized_reference_text(transcript.text)
        == normalized_reference_text(candidate.transcript or "")
    ]
    ranked = sorted(
        verified, key=lambda candidate: reference_score(candidate.metrics), reverse=True
    )
    originals = sorted(
        results, key=lambda candidate: reference_score(candidate.metrics), reverse=True
    )
    return tuple((ranked[: MAX_CANDIDATES - 1] + originals)[:MAX_CANDIDATES]), None
