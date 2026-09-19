from __future__ import annotations

import fcntl
import sys
from pathlib import Path

import pytest

from voice_of_karina.notification_storage import Change, commit_changes, locked_directories


def test_transaction_restores_prior_settings_when_later_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    first, second = tmp_path / "first.json", tmp_path / "second.toml"
    _ = first.write_bytes(b"original settings")
    changes = (Change(first, b"new settings"), Change(second, b"new config"))
    replace = Path.replace

    def fail_second(path: Path, target: str | Path) -> Path:
        if Path(target) == second:
            raise PermissionError
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_second)
    # When / Then
    with pytest.raises(PermissionError):
        _ = commit_changes(changes, frozenset({first, second}))
    assert first.read_bytes() == b"original settings"
    assert not second.exists()
    assert next(tmp_path.glob("first.json.vok-*.bak")).read_bytes() == b"original settings"


def test_installer_lock_fails_promptly_when_another_install_is_running(tmp_path: Path) -> None:
    # Given
    with (tmp_path / ".voice-of-karina-install.lock").open("a+b") as existing:
        fcntl.flock(existing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # When / Then
        with pytest.raises(BlockingIOError), locked_directories((tmp_path,)):
            pytest.fail("A conflicting install must not enter its mutation section.")


def test_replacing_asset_keeps_original_config_backup_when_backup_already_exists(
    tmp_path: Path,
) -> None:
    # Given
    config = tmp_path / "settings.json"
    _ = config.write_bytes(b"original")
    _, backups = commit_changes((Change(config, b"first version"),), frozenset({config}))
    backup = Path(backups[0])
    # When
    _ = commit_changes((Change(config, b"second version"),), frozenset({config}))
    # Then
    assert backup.read_bytes() == b"original"
    assert config.read_bytes() == b"second version"


def test_install_lock_reports_platform_error_before_mutating_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    target = tmp_path / "uncreated"
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        # When / Then
        with pytest.raises(OSError, match="requires macOS or Linux"), locked_directories((target,)):
            pytest.fail("Windows must receive an explicit unsupported-platform result.")
    assert not target.exists()
