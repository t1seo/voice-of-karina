"""Locked, rollback-capable writes for agent notification configuration."""

from __future__ import annotations

import errno
import hashlib
import os
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


@dataclass(frozen=True, slots=True)
class Change:
    """One planned file replacement, prepared before any configuration write."""

    path: Path
    data: bytes


@contextmanager
def locked_directories(directories: tuple[Path, ...]) -> Generator[None]:
    """Serialize installers across selected tool homes in a stable order."""
    if sys.platform == "win32":
        raise OSError(errno.ENOSYS, "Notification installation requires macOS or Linux.")
    import fcntl

    with ExitStack() as stack:
        for directory in sorted(set(directories)):
            directory.mkdir(parents=True, exist_ok=True)
            lock = stack.enter_context((directory / ".voice-of-karina-install.lock").open("a+b"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".vok-", delete=False) as stream:
            temporary = Path(stream.name)
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _ = temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def commit_changes(
    changes: tuple[Change, ...],
    configs: frozenset[Path],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Back up existing settings and restore every changed file on write failure."""
    snapshots = {
        change.path: change.path.read_bytes() if change.path.exists() else None
        for change in changes
    }
    pending = tuple(change for change in changes if snapshots[change.path] != change.data)
    applied: list[Path] = []
    backups: list[str] = []
    try:
        for change in pending:
            original = snapshots[change.path]
            if change.path in configs and original is not None:
                digest = hashlib.sha256(original).hexdigest()[:12]
                backup = change.path.with_name(f"{change.path.name}.vok-{digest}.bak")
                if not backup.exists():
                    _atomic_write(backup, original)
                backups.append(str(backup))
            _atomic_write(change.path, change.data)
            applied.append(change.path)
    except OSError:
        for path in reversed(applied):
            original = snapshots[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, original)
        raise
    return tuple(str(path) for path in applied), tuple(backups)
