"""Platform capability checks and one shared Metal inference lock."""

import os
import platform
import sys
from collections.abc import Generator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path
from time import monotonic, sleep
from typing import Final

from voice_of_karina.errors import VoiceError

_MIN_MACOS_MAJOR: Final = 14
_MLX_AUDIO_VERSION: Final = "0.5.4"


def require_local_runtime() -> None:
    """Fail before model downloads on unsupported hardware or missing extras."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise VoiceError(
            "unsupported_device", "Local generation requires macOS 14+ on Apple Silicon (arm64)."
        )
    major = platform.mac_ver()[0].partition(".")[0]
    if not major.isdecimal() or int(major) < _MIN_MACOS_MAJOR:
        raise VoiceError("unsupported_device", "The local MLX runtime requires macOS 14 or newer.")
    if find_spec("mlx_audio") is None:
        raise VoiceError(
            "missing_local_runtime", "Install the local runtime with uv sync --extra local."
        )
    try:
        installed = version("mlx-audio")
    except PackageNotFoundError as error:
        raise VoiceError(
            "missing_local_runtime", "Install the local runtime with uv sync --extra local."
        ) from error
    if installed != _MLX_AUDIO_VERSION:
        raise VoiceError(
            "unsupported_model_runtime", "Restore mlx-audio 0.5.4 with uv sync --extra local."
        )


@contextmanager
def inference_lock(timeout_seconds: float = 600.0) -> Generator[None, None, None]:
    """Serialize independent jobs so Whisper and TTS cannot exhaust unified memory."""
    import fcntl

    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    lock_path = cache / "voice-of-karina" / "model.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = monotonic() + timeout_seconds
    with lock_path.open("a", encoding="utf-8") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if monotonic() >= deadline:
                    raise VoiceError(
                        "model_busy",
                        "Another local voice job still owns the model runtime. Resume later.",
                    ) from error
                sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
