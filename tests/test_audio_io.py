from __future__ import annotations

import math
import struct
import wave
from typing import TYPE_CHECKING

import pytest

from voice_of_karina import audio_io
from voice_of_karina.errors import VoiceError

if TYPE_CHECKING:
    from pathlib import Path


def write_wave(path: Path, rate: int = 48000, seconds: float = 1.0) -> Path:
    samples = (
        int(8000 * math.sin(index / rate * 440 * math.tau)) for index in range(int(rate * seconds))
    )
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
    return path


def test_crop_preserves_native_sample_rate_and_requested_interval(tmp_path: Path) -> None:
    # Given a recording whose original sample rate exceeds ASR's input rate.
    source = write_wave(tmp_path / "source.wav", seconds=2)
    # When an exact reference interval is cropped.
    result = audio_io.crop_audio(source, tmp_path / "crop.wav", 0.25, 1.25)
    # Then the reference preserves its native rate and interval length.
    with wave.open(str(result), "rb") as cropped:
        assert (cropped.getframerate(), cropped.getnframes()) == (48000, 48000)


def test_decode_resamples_only_when_requested(tmp_path: Path) -> None:
    # Given the original reference recording.
    source = write_wave(tmp_path / "source.wav")
    # When a separate analysis signal is decoded.
    result = audio_io.decode_audio(source, sample_rate=16000)
    # Then ASR/VAD receive a bounded mono signal at their own rate.
    assert result.sample_rate == 16000
    assert len(result.samples) == 16000


def test_decode_rejects_a_missing_file(tmp_path: Path) -> None:
    # Given an absent generated artifact.
    missing = tmp_path / "missing.wav"
    # When the validator attempts to decode it, then failure is actionable.
    with pytest.raises(VoiceError, match=r"missing|read|decode"):
        _ = audio_io.decode_audio(missing)
