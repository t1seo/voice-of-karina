"""Canonical, bounded source acquisition."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final
from urllib.parse import parse_qs, urlparse

from voice_of_karina.audio_io import crop_audio, probe_audio, run_media
from voice_of_karina.contracts import Source
from voice_of_karina.errors import VoiceError

SCAN_SECONDS: Final = 180.0
MAX_LOCAL_BYTES: Final = 512 * 1024 * 1024
YOUTUBE_HOSTS: Final = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
EMBED_PATH_PARTS: Final = 2


def canonical_youtube_url(value: str) -> str:
    """Accept only a public YouTube video URL and discard incidental parameters."""
    try:
        parsed = urlparse(value)
    except ValueError as error:
        code = "invalid_source"
        raise VoiceError(
            code, "The video URL is malformed; provide a direct YouTube link."
        ) from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in YOUTUBE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc != parsed.hostname
    ):
        msg = "invalid_source"
        raise VoiceError(msg, "Use a direct youtube.com or youtu.be video link.")
    parts = parsed.path.strip("/").split("/")
    video = ""
    if parsed.hostname == "youtu.be" and len(parts) == 1:
        video = parts[0]
    elif parsed.path == "/watch":
        video = parse_qs(parsed.query).get("v", [""])[0]
    elif len(parts) == EMBED_PATH_PARTS and parts[0] in {"shorts", "embed", "live"}:
        video = parts[1]
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", video) is None:
        msg = "invalid_source"
        raise VoiceError(msg, "The YouTube link needs a valid individual video ID.")
    return f"https://www.youtube.com/watch?v={video}"


def file_sha256(path: Path) -> str:
    """Fingerprint bytes, never a user-controlled filename."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def validate_source(value: str) -> None:
    """Reject unsupported inputs before creating any workflow or cache files."""
    if "://" in value:
        _ = canonical_youtube_url(value)
        return
    local = Path(value).expanduser()
    if local.suffix.lower() != ".wav" or not local.is_file():
        code = "invalid_source"
        raise VoiceError(code, "Provide an existing local WAV file or a YouTube link.")
    if local.stat().st_size > MAX_LOCAL_BYTES:
        code = "source_too_large"
        raise VoiceError(code, "The local WAV exceeds 512 MiB; provide a shorter clip.")


def acquire_source(
    value: str,
    cache_dir: Path,
    *,
    start: float = 0,
    duration: float = SCAN_SECONDS,
    strict_interval: bool = False,
) -> Source:
    """Cache one bounded source interval without mixing different recordings."""
    if not 0 < duration <= SCAN_SECONDS or start < 0:
        msg = "invalid_interval"
        raise VoiceError(msg, "Select an interval of at most 180 seconds.")
    validate_source(value)
    local = Path(value).expanduser()
    if "://" not in value:
        original = str(local.resolve())
        identity = file_sha256(local)
    else:
        original = canonical_youtube_url(value)
        identity = original
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(f"{identity}:{start:.6f}:{duration:.6f}:raw-v1".encode()).hexdigest()
    target = cache_dir / f"{cache_key}.wav"
    if not target.is_file():
        if "://" not in value:
            info = probe_audio(local)
            if start >= info.duration_seconds:
                msg = "invalid_interval"
                raise VoiceError(msg, "The chosen start time is beyond this recording.")
            _ = crop_audio(local, target, start, min(start + duration, info.duration_seconds))
        else:
            _download(original, target, start, duration)
    info = probe_audio(target)
    if strict_interval and info.duration_seconds + 0.05 < duration:
        code = "invalid_interval"
        raise VoiceError(code, "The selected end time exceeds the available recording.")
    return Source(
        id=f"source-{cache_key[:20]}",
        original=original,
        audio_path=str(target.resolve()),
        sha256=file_sha256(target),
        duration_seconds=info.duration_seconds,
        sample_rate=info.sample_rate,
        offset_seconds=start,
    )


def _download(url: str, target: Path, start: float, duration: float) -> None:
    with TemporaryDirectory(prefix="download-", dir=target.parent) as temporary:
        directory = Path(temporary)
        _ = run_media(
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--ignore-config",
                "--no-playlist",
                "--no-progress",
                "--no-warnings",
                "--socket-timeout",
                "20",
                "--retries",
                "2",
                "--fragment-retries",
                "2",
                "--max-filesize",
                "100M",
                "--download-sections",
                f"*{start}-{start + duration}",
                "--force-keyframes-at-cuts",
                "-f",
                "bestaudio",
                "-o",
                str(directory / "source.%(ext)s"),
                "--",
                url,
            ],
            timeout=300,
        )
        files = tuple(
            path for path in directory.iterdir() if path.is_file() and path.suffix != ".part"
        )
        if len(files) != 1:
            msg = "download_failed"
            raise VoiceError(msg, "No complete audio was downloaded. Try another video.")
        _ = crop_audio(files[0], target, 0, min(duration, probe_audio(files[0]).duration_seconds))
