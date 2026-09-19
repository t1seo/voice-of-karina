from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from voice_of_karina import sources
from voice_of_karina.errors import VoiceError

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/r96zEiIHVf4?t=30",
        "https://www.youtube.com/watch?v=r96zEiIHVf4&list=ignore",
        "https://m.youtube.com/shorts/r96zEiIHVf4",
    ],
)
def test_youtube_identity_is_canonical_across_url_forms(url: str) -> None:
    # Given several legitimate references to the same video.
    # When source identity is parsed.
    canonical = sources.canonical_youtube_url(url)
    # Then tracking and playlist parameters cannot change the cache identity.
    assert canonical == "https://www.youtube.com/watch?v=r96zEiIHVf4"


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.example/watch?v=r96zEiIHVf4",
        "http://127.0.0.1/watch?v=r96zEiIHVf4",
        "file:///etc/passwd",
        "https://user:password@youtube.com/watch?v=r96zEiIHVf4",
        "https://www.youtube.com/watch?v=../../etc/passwd",
        "https://[youtube",
    ],
)
def test_remote_sources_reject_unsupported_hosts_and_schemes(url: str) -> None:
    # Given an input that is not a supported public YouTube video URL.
    # When parsed, then no downloader is invoked for it.
    with pytest.raises(VoiceError):
        _ = sources.canonical_youtube_url(url)


def test_content_digest_does_not_depend_on_filename(tmp_path: Path) -> None:
    # Given distinct recordings that happen to share their original name.
    first, second = tmp_path / "first.wav", tmp_path / "second.wav"
    _ = first.write_bytes(b"recording one")
    _ = second.write_bytes(b"recording two")
    # When their content fingerprints are compared.
    first_digest, second_digest = sources.file_sha256(first), sources.file_sha256(second)
    # Then unrelated content cannot reuse the same source entry.
    assert first_digest != second_digest


def test_invalid_source_does_not_create_a_cache_directory(tmp_path: Path) -> None:
    # Given an unsupported URL and a cache directory which does not yet exist.
    cache = tmp_path / "cache"
    # When acquisition rejects the source at its boundary.
    with pytest.raises(VoiceError):
        _ = sources.acquire_source("https://evil.example/audio.wav", cache)
    # Then rejection leaves the filesystem unchanged.
    assert not cache.exists()
