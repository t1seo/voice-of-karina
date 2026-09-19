"""Standalone stdlib player copied beside each tool's completion WAV."""

from __future__ import annotations

import json
import logging
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, assert_never

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type Tool = Literal["claude", "codex"]
PAYLOAD_POSITION: Final = 2
LOGGER: Final = logging.getLogger("voice-of-karina.notification")


def is_completion(
    tool: Tool,
    payload: str,
    decode: Callable[[str], JsonValue] = json.loads,
) -> bool:
    """Accept only known completion events; malformed input never plays a sound."""
    try:
        value = decode(payload)
    except json.JSONDecodeError:
        return False
    if not isinstance(value, dict):
        return False
    match tool:
        case "claude":
            return value.get("hook_event_name") == "Stop"
        case "codex":
            return value.get("type") == "agent-turn-complete"
        case unreachable:
            assert_never(unreachable)


def player_command(sound_path: Path) -> tuple[str, ...] | None:
    """Resolve the native audio player without interpreting shell text."""
    names = ("afplay",) if platform.system() == "Darwin" else ("paplay", "aplay", "ffplay")
    for name in names:
        executable = shutil.which(name)
        if executable:
            arguments = ("-nodisp", "-autoexit", "-loglevel", "error") if name == "ffplay" else ()
            return (executable, *arguments, str(sound_path))
    return None


def dispatch(tool: Tool, payload: str, install_dir: Path) -> int:
    """Forward existing Codex notifications and play this tool's completion WAV."""
    previous = install_dir / "previous-notify.txt"
    commands: list[Sequence[str]] = []
    if tool == "codex" and previous.is_file():
        old = shlex.split(previous.read_text(encoding="utf-8"))
        if old:
            commands.append((*old, payload))
    if is_completion(tool, payload):
        sound = install_dir / "complete.wav"
        command = player_command(sound) if sound.is_file() else None
        if command:
            commands.append(command)
    processes: list[subprocess.Popen[bytes]] = []
    for command in commands:
        try:
            process = subprocess.Popen(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            processes.append(process)
        except OSError as exc:
            LOGGER.warning("Notification playback failed: %s", exc)
    return 0


def main() -> int:
    """Handle Claude's stdin payload or Codex's final JSON argument."""
    if len(sys.argv) < PAYLOAD_POSITION or sys.argv[1] not in ("claude", "codex"):
        return 0
    tool: Tool = "claude" if sys.argv[1] == "claude" else "codex"
    payload = (
        sys.argv[PAYLOAD_POSITION]
        if tool == "codex" and len(sys.argv) > PAYLOAD_POSITION
        else sys.stdin.read()
    )
    try:
        return dispatch(tool, payload, Path(__file__).resolve().parent)
    except (OSError, ValueError) as exc:
        LOGGER.warning("Notification dispatch failed: %s", exc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
