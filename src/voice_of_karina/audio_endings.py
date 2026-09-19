"""Acoustic ending evidence; these energy measurements do not recognize phonemes."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from voice_of_karina.audio_metrics import rms_dbfs
from voice_of_karina.errors import VoiceError

if TYPE_CHECKING:
    from voice_of_karina.audio_io import AudioData

_FRAME_SECONDS: Final = 0.01
_BODY_RANGE_DB: Final = 40.0
_SILENCE_FLOOR_DBFS: Final = -120.0
_HIGH_OFFSET_DB: Final = 12.0
_LOW_OFFSET_DB: Final = 30.0
_ABRUPT_SECONDS: Final = 0.04
_MIN_DROP_DB: Final = 18.0
_HANGUL_START: Final = 0xAC00
_HANGUL_END: Final = 0xD7A3
_JONGSEONG_COUNT: Final = 28


@dataclass(frozen=True, slots=True)
class EndpointEvidence:
    """Observed decay/drop, with unknown measurements kept distinct from clean endings."""

    decay_seconds: float | None
    drop_db: float | None
    abrupt: bool
    ends_active: bool


_NO_EVIDENCE: Final = EndpointEvidence(None, None, abrupt=False, ends_active=False)


def _frame_levels(audio: AudioData, frame_size: int) -> tuple[float, ...]:
    """Ignore partial frames instead of diluting their RMS with fictional zero samples."""
    levels: list[float] = []
    for start in range(0, len(audio.samples) - frame_size + 1, frame_size):
        level = rms_dbfs(audio.samples[start : start + frame_size])
        levels.append(max(_SILENCE_FLOOR_DBFS, level) if level is not None else _SILENCE_FLOOR_DBFS)
    return tuple(levels)


def endpoint_evidence(audio: AudioData) -> EndpointEvidence:
    """Measure the final energy decay independently of appended silence duration.

    A fast observed drop is only cut-risk evidence. A real plosive or a naturally
    brief ending can have the same shape; callers must restrict its applicability.
    Body selection and both decay thresholds are relative to signal level, so
    ordinary gain changes do not move the measured ending. Silence is floored
    at -120 dBFS; evidence is unavailable below the supported measurement range.
    """
    if audio.sample_rate <= 0:
        raise VoiceError("invalid_sample_rate", "Endpoint analysis needs a positive sample rate.")
    if not all(math.isfinite(sample) for sample in audio.samples):
        raise VoiceError("nonfinite_audio", "Audio contains nonfinite (NaN or infinite) samples.")
    frame_size = max(1, round(audio.sample_rate * _FRAME_SECONDS))
    levels = _frame_levels(audio, frame_size)
    active_floor = max(
        _SILENCE_FLOOR_DBFS, max(levels, default=_SILENCE_FLOOR_DBFS) - _BODY_RANGE_DB
    )
    nonquiet = sorted(level for level in levels if level > active_floor)
    if not nonquiet:
        return _NO_EVIDENCE
    position = (len(nonquiet) - 1) * 0.9
    left = math.floor(position)
    body = nonquiet[left] + (nonquiet[math.ceil(position)] - nonquiet[left]) * (position - left)
    high = body - _HIGH_OFFSET_DB
    low = body - _LOW_OFFSET_DB
    if low <= _SILENCE_FLOOR_DBFS:
        return _NO_EVIDENCE
    last_high = max(index for index, level in enumerate(levels) if level >= high)
    last_low = max(index for index, level in enumerate(levels) if level >= low)
    decay = (last_low - last_high) * frame_size / audio.sample_rate
    drop = levels[last_high] - levels[last_low + 1] if last_low + 1 < len(levels) else None
    return EndpointEvidence(
        decay_seconds=decay,
        drop_db=drop,
        abrupt=decay < _ABRUPT_SECONDS and drop is not None and drop >= _MIN_DROP_DB,
        ends_active=levels[-1] >= high,
    )


def requested_text_ends_with_korean_open_vowel(text: str) -> bool:
    """Check the final normalized letter or number for a Hangul syllable without jongseong."""
    for character in reversed(unicodedata.normalize("NFKC", text)):
        if not character.isalnum():
            continue
        point = ord(character)
        return (
            _HANGUL_START <= point <= _HANGUL_END
            and (point - _HANGUL_START) % _JONGSEONG_COUNT == 0
        )
    return False
