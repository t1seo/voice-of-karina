"""Offer native crops only when ASR boundaries have measured quiet neighborhoods."""

from __future__ import annotations

import hashlib
import math
import struct
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import webrtcvad

from voice_of_karina.audio_io import AudioData, crop_audio, decode_audio
from voice_of_karina.audio_metrics import (
    ANALYSIS_RATE,
    FRAME_SECONDS,
    MAX_REFERENCE_SECONDS,
    MIN_REFERENCE_SECONDS,
    SpeechFrame,
    reference_metrics,
    speech_frames,
)
from voice_of_karina.contracts import Candidate

if TYPE_CHECKING:
    from voice_of_karina.backends.stt import TranscriptSegment

MAX_BOUNDARY_DISTANCE: Final = 0.45
MIN_SPEECH_MARGIN: Final = 0.09
PREFERRED_SPEECH_MARGIN: Final = 0.12
MAX_QUIET_RMS: Final = 0.01
QUIET_TO_PEAK_RATIO: Final = 0.1
MIN_UTTERANCE_SEGMENTS: Final = 2
BOUNDARY_VAD_FRAME_SECONDS: Final = 0.01
BOUNDARY_VAD_AGGRESSIVENESS: Final = 3


@dataclass(frozen=True, slots=True)
class UtteranceWindow:
    """A source-relative native crop with its independently verifiable intended text."""

    start: float
    end: float
    text: str


def pause_analysis_frames(audio: AudioData) -> tuple[SpeechFrame, ...]:
    """Aggregate continuous 10ms VAD decisions into 30ms boundary-only energy frames."""
    detector = webrtcvad.Vad(BOUNDARY_VAD_AGGRESSIVENESS)
    fine_size = round(ANALYSIS_RATE * BOUNDARY_VAD_FRAME_SECONDS)
    coarse_size = round(ANALYSIS_RATE * FRAME_SECONDS)
    frames: list[SpeechFrame] = []
    for index, measured in enumerate(speech_frames(audio)):
        decisions: list[bool] = []
        first = index * coarse_size
        for start in range(first, first + coarse_size, fine_size):
            samples = audio.samples[start : start + fine_size]
            pcm = b"".join(
                struct.pack("<h", round(max(-1.0, min(1.0, value)) * 32767)) for value in samples
            )
            decisions.append(detector.is_speech(pcm, ANALYSIS_RATE))
        frames.append(SpeechFrame(speech=any(decisions), rms=measured.rms))
    return tuple(frames)


def normalized_reference_text(text: str) -> str:
    """Compare ASR wording without Unicode width, spacing, or punctuation differences."""
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum()
    )


def valid_segment_order(segments: tuple[TranscriptSegment, ...], duration: float) -> bool:
    """Require nonempty, ordered ASR intervals inside the actual candidate crop."""
    previous_end = 0.0
    for part in segments:
        if (
            not math.isfinite(part.start)
            or not math.isfinite(part.end)
            or not previous_end <= part.start < part.end <= duration
            or not normalized_reference_text(part.text)
        ):
            return False
        previous_end = part.end
    return bool(segments)


def utterance_reference_windows(
    frames: tuple[SpeechFrame, ...],
    segments: tuple[TranscriptSegment, ...],
    duration: float,
) -> tuple[UtteranceWindow, ...]:
    """Require 60ms of quiet around each cut and retain measured speech margins."""
    if len(segments) < MIN_UTTERANCE_SEGMENTS or not valid_segment_order(segments, duration):
        return ()
    quiet_limit = min(
        MAX_QUIET_RMS, max((frame.rms for frame in frames), default=0) * QUIET_TO_PEAK_RATIO
    )
    quiet = tuple(not frame.speech and frame.rms <= quiet_limit for frame in frames)
    cuts = tuple(
        round(index * FRAME_SECONDS, 6)
        for index in range(1, len(frames))
        if quiet[index - 1] and quiet[index]
    )
    windows: list[UtteranceWindow] = []
    for index, part in enumerate(segments):
        lower = segments[index - 1].end if index else 0
        upper = segments[index + 1].start if index + 1 < len(segments) else duration
        starts = tuple(
            cut
            for cut in cuts
            if lower <= cut and MIN_SPEECH_MARGIN <= part.start - cut <= MAX_BOUNDARY_DISTANCE
        )
        ends = tuple(
            cut
            for cut in cuts
            if cut <= upper and MIN_SPEECH_MARGIN <= cut - part.end <= MAX_BOUNDARY_DISTANCE
        )
        options = tuple(
            (start, end)
            for start in starts
            for end in ends
            if MIN_REFERENCE_SECONDS <= round(end - start, 6) <= MAX_REFERENCE_SECONDS
            and end - start < duration
        )
        if options:
            start, end = min(
                options,
                key=lambda pair: (
                    abs(part.start - pair[0] - PREFERRED_SPEECH_MARGIN)
                    + abs(pair[1] - part.end - PREFERRED_SPEECH_MARGIN)
                ),
            )
            windows.append(UtteranceWindow(start, end, part.text.strip()))
    return tuple(windows)


def pause_bounded_candidates(
    parent: Candidate,
    segments: tuple[TranscriptSegment, ...],
    job_dir: Path,
    *,
    limit: int,
) -> tuple[Candidate, ...]:
    """Crop native PCM with absolute provenance; callers must verify each transcript."""
    if limit <= 0 or len(segments) < MIN_UTTERANCE_SEGMENTS:
        return ()
    source = Path(parent.audio_path)
    analysis = decode_audio(source, sample_rate=ANALYSIS_RATE, max_seconds=MAX_REFERENCE_SECONDS)
    duration = min(parent.end_seconds - parent.start_seconds, len(analysis.samples) / ANALYSIS_RATE)
    windows = utterance_reference_windows(pause_analysis_frames(analysis), segments, duration)[
        :limit
    ]
    candidates: list[Candidate] = []
    for window in windows:
        identifier = hashlib.sha256(
            f"pause:{parent.id}:{window.start:.6f}:{window.end:.6f}".encode()
        ).hexdigest()[:20]
        clip = job_dir / "candidates" / f"candidate-{identifier}.wav"
        _ = crop_audio(source, clip, window.start, window.end)
        samples = analysis.samples[
            int(window.start * ANALYSIS_RATE) : int(window.end * ANALYSIS_RATE)
        ]
        candidates.append(
            Candidate(
                id=f"candidate-{identifier}",
                source_id=parent.source_id,
                source=parent.source,
                audio_path=str(clip.resolve()),
                start_seconds=parent.start_seconds + window.start,
                end_seconds=parent.start_seconds + window.end,
                transcript=window.text,
                metrics=reference_metrics(AudioData(samples, ANALYSIS_RATE)),
                warnings=(
                    *parent.warnings,
                    (
                        "Pause-bounded analysis candidate: measured pauses and matching ASR do not "
                        "guarantee speaker identity or naturalness."
                    ),
                ),
                needs_confirmation=parent.needs_confirmation,
                pause_bounded=True,
                utterance_count=1,
            )
        )
    return tuple(candidates)
