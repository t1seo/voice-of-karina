from __future__ import annotations

import struct
import wave
from dataclasses import dataclass
from typing import TYPE_CHECKING

from voice_of_karina.backends.generation import write_audio

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class Samples:
    values: tuple[float, ...]

    def reshape(self, _size: int) -> Samples:
        return self

    def tolist(self) -> list[float]:
        return list(self.values)


@dataclass(frozen=True, slots=True)
class Chunk:
    audio: Samples
    sample_rate: int = 24_000


def test_wav_preserves_chunk_order_and_clamps_pcm_when_model_samples_exceed_range(
    tmp_path: Path,
) -> None:
    # Given
    chunks = (Chunk(Samples((-2.0, -0.5))), Chunk(Samples((0.5, 2.0))))
    path = tmp_path / "joined.wav"
    # When
    stats = write_audio(iter(chunks), path)
    # Then
    with wave.open(str(path), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 24_000
        assert audio.getnframes() == 4
        assert struct.unpack("<4h", audio.readframes(4)) == (-32767, -16384, 16384, 32767)
    assert stats.sample_rate == 24_000
    assert stats.duration_seconds == 4 / 24_000
