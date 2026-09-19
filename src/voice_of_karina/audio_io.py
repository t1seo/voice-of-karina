"""Bounded decoding and native-rate reference crops."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from voice_of_karina.errors import VoiceError

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class AudioInfo:
    """Physical stream properties reported by ffprobe."""

    sample_rate: int
    channels: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class AudioData:
    """Mono float samples decoded without gain or noise processing."""

    samples: array[float]
    sample_rate: int


class _Stream(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    sample_rate: int
    channels: int


class _Format(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    duration: float


class _Probe(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    streams: tuple[_Stream, ...]
    format: _Format


def run_media(command: Sequence[str], *, timeout: float = 120) -> bytes:
    """Run an argument-vector media command with a deadline and bounded error text."""
    if not command or shutil.which(command[0]) is None:
        msg = "missing_dependency"
        raise VoiceError(msg, "Install ffmpeg and ffprobe, then retry.")
    try:
        with subprocess.Popen(  # noqa: S603
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        ) as process:
            try:
                output, errors = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                os.killpg(process.pid, signal.SIGKILL)
                _ = process.communicate()
                msg = "media_timeout"
                raise VoiceError(msg, "Audio processing exceeded its time limit.") from error
            if process.returncode != 0:
                detail = errors.decode("utf-8", errors="replace")[-600:]
                msg = "media_failed"
                raise VoiceError(msg, f"Cannot read/decode the audio: {detail}")
    except OSError as error:
        msg = "media_failed"
        raise VoiceError(msg, f"Cannot read/decode the audio: {error}") from error
    return output


def probe_audio(path: Path) -> AudioInfo:
    """Parse actual stream metadata before allocating a sample buffer."""
    if not path.is_file():
        msg = "missing_audio"
        raise VoiceError(msg, f"Audio file is missing: {path}")
    raw = run_media(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels:format=duration",
            "-of",
            "json",
            str(path.resolve()),
        ]
    )
    try:
        result = _Probe.model_validate_json(raw)
    except ValidationError as error:
        msg = "invalid_audio"
        raise VoiceError(msg, "The audio stream has no readable duration or rate.") from error
    if not result.streams or result.streams[0].sample_rate <= 0:
        msg = "invalid_audio"
        raise VoiceError(msg, "The file has no usable audio stream.")
    return AudioInfo(
        result.streams[0].sample_rate, result.streams[0].channels, result.format.duration
    )


def decode_audio(
    path: Path,
    *,
    sample_rate: int | None = None,
    max_seconds: float = 181,
) -> AudioData:
    """Decode bounded float PCM; resample only the separate analysis signal."""
    info = probe_audio(path)
    rate = sample_rate if sample_rate is not None else info.sample_rate
    raw = run_media(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path.resolve()),
            "-t",
            str(max_seconds),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-c:a",
            "pcm_f32le",
            "-f",
            "f32le",
            "pipe:1",
        ]
    )
    samples: array[float] = array("f")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    return AudioData(samples, rate)


def crop_audio(
    source: Path,
    target: Path,
    start: float,
    end: float,
    *,
    sample_rate: int | None = None,
) -> Path:
    """Atomically crop a mono WAV, retaining the original rate unless requested."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(suffix=".wav", dir=target.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source.resolve()),
        "-ss",
        str(start),
        "-t",
        str(end - start),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
    ]
    if sample_rate is not None:
        command.extend(["-ar", str(sample_rate)])
    command.append(str(temporary_path))
    try:
        _ = run_media(command)
        _ = temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)
    return target
