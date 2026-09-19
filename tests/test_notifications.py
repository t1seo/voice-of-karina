from __future__ import annotations

import json
import os
import struct
import sys
import tomllib
import wave
from typing import TYPE_CHECKING

import pytest

from voice_of_karina import notifications

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sound(tmp_path: Path) -> Path:
    path = tmp_path / "ready.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(struct.pack("<hh", 2400, -2400) * 6000)
    return path


def test_install_preserves_existing_settings_when_both_tools_selected(
    tmp_path: Path,
    sound: Path,
) -> None:
    # Given
    claude = tmp_path / "claude home"
    codex = tmp_path / "codex home"
    claude.mkdir()
    codex.mkdir()
    claude_config = claude / "settings.json"
    codex_config = codex / "config.toml"
    _ = claude_config.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Read"]},
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "echo existing", "timeout": 0.5}
                            ]
                        },
                    ]
                },
            }
        ),
    )
    _ = codex_config.write_text(
        '# keep comment\nmodel = "other-model"\nnotify = ["old-notify", "arg"]\n'
    )
    # When
    result = notifications.install_notifications(
        sound,
        ("claude", "codex"),
        claude_dir=claude,
        codex_dir=codex,
    )
    # Then
    assert result.status == "installed"
    assert '"allow": [' in claude_config.read_text()
    assert "echo existing" in claude_config.read_text()
    assert '"timeout": 0.5' in claude_config.read_text()
    assert "# keep comment" in codex_config.read_text()
    assert 'model = "other-model"' in codex_config.read_text()
    assert len(result.backups) == 2
    assert (codex / "voice-of-karina" / "previous-notify.txt").read_text() == "old-notify arg"


def test_install_is_idempotent_when_same_sound_is_applied_twice(
    tmp_path: Path,
    sound: Path,
) -> None:
    # Given
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    _ = notifications.install_notifications(
        sound, ("claude", "codex"), claude_dir=claude, codex_dir=codex
    )
    before = (claude / "settings.json").read_bytes(), (codex / "config.toml").read_bytes()
    # When
    result = notifications.install_notifications(
        sound,
        ("claude", "codex"),
        claude_dir=claude,
        codex_dir=codex,
    )
    # Then
    assert result.changed_paths == ()
    assert before == ((claude / "settings.json").read_bytes(), (codex / "config.toml").read_bytes())


@pytest.mark.parametrize("invalid", ["not json", '{"hooks": []}'])
def test_install_leaves_files_unchanged_when_claude_settings_invalid(
    tmp_path: Path,
    sound: Path,
    invalid: str,
) -> None:
    # Given
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    claude.mkdir()
    config = claude / "settings.json"
    _ = config.write_text(invalid)
    # When / Then
    with pytest.raises(notifications.NotificationInstallError):
        _ = notifications.install_notifications(
            sound,
            ("codex", "claude"),
            claude_dir=claude,
            codex_dir=codex,
        )
    assert config.read_text() == invalid
    assert not (codex / "config.toml").exists()
    assert not (claude / "voice-of-karina").exists()


def test_install_rejects_invalid_wav_without_touching_configs(tmp_path: Path) -> None:
    # Given
    sound = tmp_path / "not-a-sound.wav"
    _ = sound.write_bytes(b"invalid")
    claude = tmp_path / "claude"
    # When / Then
    with pytest.raises(notifications.NotificationInstallError):
        _ = notifications.install_notifications(sound, ("claude",), claude_dir=claude)
    assert not claude.exists()


def test_install_scopes_sound_to_selected_tool_when_only_codex_requested(
    tmp_path: Path,
    sound: Path,
) -> None:
    # Given
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    # When
    _ = notifications.install_notifications(sound, ("codex",), claude_dir=claude, codex_dir=codex)
    # Then
    assert not claude.exists()
    assert (codex / "voice-of-karina" / "complete.wav").read_bytes() == sound.read_bytes()
    assert "voice-of-karina" in json.dumps(tomllib.loads((codex / "config.toml").read_text()))


def test_reinstall_keeps_original_notifier_when_python_path_changes(
    tmp_path: Path,
    sound: Path,
) -> None:
    # Given
    codex = tmp_path / "codex"
    codex.mkdir()
    config = codex / "config.toml"
    _ = config.write_text('notify = ["original-program", "argument"]\n')
    _ = notifications.install_notifications(sound, ("codex",), codex_dir=codex)
    _ = config.write_text(
        config.read_text().replace(os.path.realpath(sys.executable), "/old/python")
    )
    # When
    _ = notifications.install_notifications(sound, ("codex",), codex_dir=codex)
    # Then
    assert (
        codex / "voice-of-karina" / "previous-notify.txt"
    ).read_text() == "original-program argument"
    assert "/old/python" not in config.read_text()
