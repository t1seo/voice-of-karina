from __future__ import annotations

import json
import shlex
import struct
import wave
from pathlib import Path

import pytest

from voice_of_karina.notifications import ClaudeSettings, install_notifications


@pytest.fixture
def sound(tmp_path: Path) -> Path:
    result = tmp_path / "generated.wav"
    with wave.open(str(result), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack("<hh", 1200, -1200) * 2400)
    return result


def test_migration_removes_only_project_legacy_hooks_when_new_sound_is_installed(
    tmp_path: Path,
    sound: Path,
) -> None:
    # Given
    legacy = Path.home() / ".local/share/voice-notification/notification_player.py"
    old_command = shlex.join(["python3", str(legacy)])
    external_command = shlex.join(["python3", str(tmp_path / "other/notification_player.py")])
    config = tmp_path / "settings.json"
    entries = [
        {"type": "command", "command": command} for command in (old_command, external_command)
    ]
    _ = config.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"hooks": entries}],
                    "Notification": [{"matcher": "permission_prompt", "hooks": entries}],
                }
            }
        )
    )
    # When
    result = install_notifications(sound, ("claude",), claude_dir=tmp_path)
    # Then
    settings = ClaudeSettings.model_validate_json(config.read_bytes())
    stop = [hook.command for group in settings.hooks["Stop"] for hook in group.hooks]
    attention = [hook.command for group in settings.hooks["Notification"] for hook in group.hooks]
    assert old_command not in (*stop, *attention)
    assert external_command in stop
    assert external_command in attention
    assert len(stop) == 2
    assert len(result.backups) == 1
    assert "permission_prompt" in config.read_text()


def test_migration_does_not_chain_legacy_notifier_when_codex_is_installed(
    tmp_path: Path,
    sound: Path,
) -> None:
    # Given
    legacy = Path.home() / ".local/share/voice-notification/notification_player.py"
    config = tmp_path / "config.toml"
    original = "notify = " + json.dumps(["python3", str(legacy)]) + "\n"
    _ = config.write_text(original)
    # When
    result = install_notifications(sound, ("codex",), codex_dir=tmp_path)
    # Then
    assert (tmp_path / "voice-of-karina/previous-notify.txt").read_text() == ""
    assert Path(result.backups[0]).read_text() == original


def test_migration_preserves_similar_external_notifier_when_codex_is_installed(
    tmp_path: Path,
    sound: Path,
) -> None:
    # Given
    external = tmp_path / "other/voice-notification/notification_player.py"
    _ = (tmp_path / "config.toml").write_text("notify = " + json.dumps(["python3", str(external)]))
    # When
    _ = install_notifications(sound, ("codex",), codex_dir=tmp_path)
    # Then
    assert (tmp_path / "voice-of-karina/previous-notify.txt").read_text() == shlex.join(
        ["python3", str(external)]
    )
