"""Signal measurements and WebRTC speech windows, without speaker identification."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import webrtcvad

from voice_of_karina.contracts import Metrics
from voice_of_karina.errors import VoiceError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from voice_of_karina.audio_io import AudioData

FRAME_SECONDS: Final = 0.03
ANALYSIS_RATE: Final = 16000
MIN_REFERENCE_SECONDS: Final = 3.0
MAX_REFERENCE_SECONDS: Final = 15.0
MIN_REFERENCE_RMS_DBFS: Final = -50.0
MAX_REFERENCE_CLIPPING: Final = 0.02
MIN_SPEECH_RATIO: Final = 0.35
NOISY_FLOOR_DBFS: Final = -35.0
CLIPPING_AMPLITUDE: Final = 0.999
UTTERANCE_PAUSE_SECONDS: Final = 0.6


@dataclass(frozen=True, slots=True)
class SpeechFrame:
    """VAD's speech decision and measured energy for a 30 millisecond frame."""

    speech: bool
    rms: float


def rms_dbfs(samples: Sequence[float]) -> float | None:
    """Represent silence as unknown logarithmic amplitude rather than negative infinity."""
    if not samples:
        return None
    mean_square = math.fsum(sample * sample for sample in samples) / len(samples)
    return 10 * math.log10(mean_square) if mean_square > 0 else None


def signal_metrics(audio: AudioData) -> Metrics:
    """Reject nonfinite PCM before measuring physical artifact validity."""
    samples = audio.samples
    if not all(math.isfinite(sample) for sample in samples):
        msg = "nonfinite_audio"
        raise VoiceError(msg, "Audio contains nonfinite (NaN or infinite) samples.")
    return Metrics(
        duration_seconds=len(samples) / audio.sample_rate,
        rms_dbfs=rms_dbfs(samples),
        peak=max((abs(x) for x in samples), default=0),
        clipping_ratio=sum(abs(x) >= CLIPPING_AMPLITUDE for x in samples) / max(len(samples), 1),
    )


def speech_frames(audio: AudioData) -> tuple[SpeechFrame, ...]:
    """Run VAD on its required 16kHz signal; never interpret this as diarization."""
    if audio.sample_rate != ANALYSIS_RATE:
        msg = "invalid_analysis_rate"
        raise VoiceError(msg, "VAD needs the separate 16kHz analysis signal.")
    detector = webrtcvad.Vad(2)
    size = int(ANALYSIS_RATE * FRAME_SECONDS)
    frames: list[SpeechFrame] = []
    for start in range(0, len(audio.samples) - size + 1, size):
        samples = audio.samples[start : start + size]
        pcm = b"".join(struct.pack("<h", round(max(-1.0, min(1.0, x)) * 32767)) for x in samples)
        rms = math.sqrt(math.fsum(x * x for x in samples) / size)
        frames.append(SpeechFrame(detector.is_speech(pcm, ANALYSIS_RATE), rms))
    return tuple(frames)


def reference_metrics(audio: AudioData) -> Metrics:
    """Estimate background level only when VAD found actual non-speech frames."""
    metrics = signal_metrics(audio)
    frames = speech_frames(audio)
    quiet = sorted(frame.rms for frame in frames if not frame.speech)
    floor = quiet[len(quiet) // 2] if quiet else None
    return metrics.model_copy(
        update={
            "speech_ratio": sum(frame.speech for frame in frames) / max(len(frames), 1),
            "noise_floor_dbfs": 20 * math.log10(floor) if floor is not None and floor > 0 else None,
        }
    )


def utterance_windows(frames: tuple[SpeechFrame, ...]) -> tuple[tuple[float, float], ...]:
    """Group speech across short pauses and prefer utterance boundaries to loud peaks."""
    voiced = [index for index, frame in enumerate(frames) if frame.speech]
    if not voiced:
        return ()
    groups: list[tuple[int, int]] = []
    first = previous = voiced[0]
    for index in voiced[1:]:
        if (index - previous) * FRAME_SECONDS > UTTERANCE_PAUSE_SECONDS:
            groups.append((first, previous + 1))
            first = index
        previous = index
    groups.append((first, previous + 1))
    windows: list[tuple[float, float]] = []
    for first, last in groups:
        start = max(0, first * FRAME_SECONDS - 0.15)
        end = min(len(frames) * FRAME_SECONDS, last * FRAME_SECONDS + 0.15)
        if end - start < MIN_REFERENCE_SECONDS:
            continue
        while end - start > MAX_REFERENCE_SECONDS:
            boundary = min(last, int((start + MAX_REFERENCE_SECONDS) / FRAME_SECONDS))
            lower = int((start + 6) / FRAME_SECONDS)
            pauses = [i for i in range(lower, boundary) if not frames[i].speech]
            cut = (pauses[-1] + 1) * FRAME_SECONDS if pauses else boundary * FRAME_SECONDS
            windows.append((start, cut))
            start = cut
        if end - start >= MIN_REFERENCE_SECONDS:
            windows.append((start, end))
    return tuple(windows)


def reference_score(metrics: Metrics) -> float:
    """Rank measured speech coverage and recording defects, not loudness alone."""
    speech = metrics.speech_ratio or 0
    clipping = metrics.clipping_ratio or 0
    floor = metrics.noise_floor_dbfs
    noise_penalty = max(0, (floor + 45) / 30) if floor is not None else 0.2
    return speech - 10 * clipping - noise_penalty + min(metrics.duration_seconds, 10) / 50
