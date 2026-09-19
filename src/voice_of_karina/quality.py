"""Measured audio and transcript validation."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

from voice_of_karina.audio_endings import (
    endpoint_evidence,
    requested_text_ends_with_korean_open_vowel,
)
from voice_of_karina.audio_io import decode_audio, probe_audio
from voice_of_karina.audio_metrics import signal_metrics
from voice_of_karina.audio_transcription import uncertain_transcript
from voice_of_karina.backends.languages import asr_language
from voice_of_karina.backends.stt import transcribe_audio
from voice_of_karina.contracts import GeneratedAudio, Metrics, QualityOptions, QualityResult
from voice_of_karina.errors import VoiceError
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION

if TYPE_CHECKING:
    from collections.abc import Callable


def normalize_text(text: str) -> str:
    """Ignore punctuation and spacing while retaining spoken letters and digits."""
    return "".join(
        char.casefold() for char in unicodedata.normalize("NFKC", text) if char.isalnum()
    )


def text_error_rate(expected: str, observed: str) -> float:
    """Compute normalized character edit distance, including insertions and deletions."""
    reference, transcript = normalize_text(expected), normalize_text(observed)
    previous = list(range(len(transcript) + 1))
    for row, left in enumerate(reference, 1):
        current = [row]
        for col, right in enumerate(transcript, 1):
            current.append(
                min(current[-1] + 1, previous[col] + 1, previous[col - 1] + (left != right))
            )
        previous = current
    return previous[-1] / max(len(reference), 1)


def validate_output(
    audio: GeneratedAudio,
    expected_text: str,
    options: QualityOptions | None = None,
    *,
    transcriber: Callable[[Path], str] | None = None,
) -> QualityResult:
    """Require decodable, finite, audible PCM plus a verified transcript before passing."""
    policy = options or QualityOptions()
    try:
        path = Path(audio.path)
        info = probe_audio(path)
        if not policy.min_duration_seconds <= info.duration_seconds <= policy.max_duration_seconds:
            return QualityResult(
                policy_version=QUALITY_POLICY_VERSION,
                valid=False,
                decision="retry",
                warnings=("Output duration is outside the allowed range.",),
            )
        decoded = decode_audio(path, max_seconds=policy.max_duration_seconds + 1)
        metrics = signal_metrics(decoded)
        ending = endpoint_evidence(decoded)
        metrics = metrics.model_copy(
            update={
                "terminal_decay_seconds": ending.decay_seconds,
                "terminal_drop_db": ending.drop_db,
            }
        )
    except VoiceError as error:
        return QualityResult(
            policy_version=QUALITY_POLICY_VERSION,
            valid=False,
            decision="retry",
            warnings=(error.message,),
        )
    warnings: list[str] = []
    if metrics.rms_dbfs is None or metrics.rms_dbfs < policy.min_rms_dbfs:
        warnings.append("Output is silent or too quiet; regenerate this message.")
    if metrics.clipping_ratio is not None and metrics.clipping_ratio > policy.max_clipping_ratio:
        warnings.append("Output is excessively clipped; regenerate this message.")
    if warnings:
        return QualityResult(
            policy_version=QUALITY_POLICY_VERSION,
            valid=False,
            decision="retry",
            metrics=metrics,
            warnings=tuple(warnings),
        )
    vowel_ending = requested_text_ends_with_korean_open_vowel(expected_text)
    if vowel_ending and ending.decay_seconds is None:
        return QualityResult(
            policy_version=QUALITY_POLICY_VERSION,
            valid=True,
            decision="needs_input",
            metrics=metrics,
            warnings=("The vowel ending could not be measured; listen before proceeding.",),
        )
    if vowel_ending and (ending.abrupt or ending.ends_active):
        return QualityResult(
            policy_version=QUALITY_POLICY_VERSION,
            valid=True,
            decision="retry",
            metrics=metrics,
            warnings=("The vowel ending may be cut short; regenerate the complete utterance.",),
        )
    return _verify_text(audio, expected_text, policy, metrics, transcriber)


def _verify_text(
    audio: GeneratedAudio,
    expected_text: str,
    policy: QualityOptions,
    metrics: Metrics,
    transcriber: Callable[[Path], str] | None,
) -> QualityResult:
    path = Path(audio.path)
    try:
        if transcriber is None:
            recognition = transcribe_audio(path, language=asr_language(audio.language))
            transcript = recognition.text
            uncertain = uncertain_transcript(recognition)
        else:
            transcript = transcriber(path)
            uncertain = False
    except VoiceError as error:
        return QualityResult(
            policy_version=QUALITY_POLICY_VERSION,
            valid=True,
            decision="needs_input",
            metrics=metrics,
            warnings=(f"Audio exists, but speech verification is unavailable: {error.message}",),
        )
    if uncertain or not normalize_text(transcript):
        warning = (
            "Speech confidence is unavailable or low; listen before proceeding."
            if uncertain
            else "No reliable transcript was recognized; listen before proceeding."
        )
        return QualityResult(
            policy_version=QUALITY_POLICY_VERSION,
            valid=True,
            decision="needs_input",
            metrics=metrics,
            transcript=transcript,
            warnings=(warning,),
        )
    error_rate = text_error_rate(expected_text, transcript)
    suffix = normalize_text(expected_text)[-4:]
    ending_matches = bool(suffix) and normalize_text(transcript).endswith(suffix)
    metrics = metrics.model_copy(
        update={"text_error_rate": error_rate, "ending_text_match": ending_matches}
    )
    if not ending_matches:
        return QualityResult(
            policy_version=QUALITY_POLICY_VERSION,
            valid=True,
            decision="retry",
            metrics=metrics,
            transcript=transcript,
            warnings=("The recognized ending differs from the requested ending.",),
        )
    if error_rate > policy.max_text_error_rate:
        return QualityResult(
            policy_version=QUALITY_POLICY_VERSION,
            valid=True,
            decision="retry",
            metrics=metrics,
            transcript=transcript,
            warnings=(
                f"Recognized speech differs from the requested text (CER {error_rate:.2f}).",
            ),
        )
    return QualityResult(
        policy_version=QUALITY_POLICY_VERSION,
        valid=True,
        decision="pass",
        metrics=metrics,
        transcript=transcript,
    )
