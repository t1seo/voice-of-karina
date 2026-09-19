from __future__ import annotations

import json
import os
import select
import shlex
import struct
import subprocess
import sys
import wave
from typing import TYPE_CHECKING

import pytest

from voice_of_karina import notification_player
from voice_of_karina.notifications import install_notifications

if TYPE_CHECKING:
    from pathlib import Path

    from voice_of_karina.notification_player import Tool


@pytest.mark.parametrize(
    ("tool", "payload", "expected"),
    [
        ("claude", '{"hook_event_name":"Stop"}', True),
        ("codex", '{"type":"agent-turn-complete"}', True),
        ("claude", '{"hook_event_name":"Notification"}', False),
        ("codex", '{"type":"unknown"}', False),
        ("codex", "not-json", False),
        ("claude", "[]", False),
        ("claude", "null", False),
    ],
)
def test_event_dispatch_when_hook_payload_arrives(
    tool: Tool,
    payload: str,
    expected: bool,
) -> None:
    # Given / When
    observed = notification_player.is_completion(tool, payload)
    # Then
    assert observed is expected


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_installed_player_dispatches_tool_scoped_wav_when_completion_arrives(
    tmp_path: Path,
    tool: Tool,
) -> None:
    # Given
    sound = tmp_path / "generated.wav"
    with wave.open(str(sound), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack("<hh", 1200, -1200) * 2400)
    target = tmp_path / "tool home with spaces"
    _ = install_notifications(sound, (tool,), claude_dir=target, codex_dir=target)
    player_dir = tmp_path / "bin"
    player_dir.mkdir()
    fifo = tmp_path / "played-path"
    os.mkfifo(fifo)
    for name in ("afplay", "paplay"):
        player = player_dir / name
        _ = player.write_text(f"#!/bin/sh\nprintf '%s' \"$1\" > {shlex.quote(str(fifo))}\n")
        player.chmod(0o700)
    installed = target / "voice-of-karina" / "player.py"
    payload = json.dumps({"hook_event_name": "Stop", "type": "agent-turn-complete"})
    command = [sys.executable, "-I", str(installed), tool]
    if tool == "codex":
        command.append(payload)
    # When
    with os.fdopen(os.open(fifo, os.O_RDWR | os.O_NONBLOCK), "rb", buffering=0) as receiver:
        result = subprocess.run(  # noqa: S603
            command,
            input=payload,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
            env={**os.environ, "PATH": str(player_dir)},
        )
        ready, _, _ = select.select([receiver], [], [], 5)
        assert ready
        played_path = receiver.read(8192)
    # Then
    assert result.returncode == 0
    assert result.stdout == ""
    assert played_path == str(target / "voice-of-karina" / "complete.wav").encode()


def test_existing_notifier_receives_original_payload_when_codex_dispatches(tmp_path: Path) -> None:
    # Given
    fifo = tmp_path / "event"
    os.mkfifo(fifo)
    old_notifier = tmp_path / "old notifier"
    _ = old_notifier.write_text(f"#!/bin/sh\nprintf '%s' \"$1\" > {shlex.quote(str(fifo))}\n")
    old_notifier.chmod(0o700)
    _ = (tmp_path / "previous-notify.txt").write_text(shlex.join([str(old_notifier)]))
    payload = '{"type":"unknown","data":"quotes \\" remain data"}'
    # When
    with os.fdopen(os.open(fifo, os.O_RDWR | os.O_NONBLOCK), "rb", buffering=0) as receiver:
        result = notification_player.dispatch("codex", payload, tmp_path)
        ready, _, _ = select.select([receiver], [], [], 5)
        assert ready
        forwarded = receiver.read(8192)
    # Then
    assert result == 0
    assert forwarded == payload.encode()
