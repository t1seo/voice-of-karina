"""Optional installation of validated completion sounds."""

from __future__ import annotations

import os
import shlex
import sys
import wave
from pathlib import Path
from typing import ClassVar, Final, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from tomlkit import dumps, parse
from tomlkit.exceptions import ParseError

from voice_of_karina.notification_storage import Change, commit_changes, locked_directories

type NotificationTool = Literal["claude", "codex"]
MAX_SOUND_BYTES: Final = 32_000_000
LEGACY_COMMAND: Final = (
    "python3",
    str(Path.home() / ".local/share/voice-notification/notification_player.py"),
)


class NotificationInstallError(Exception):
    """An installation failed before configuration could be safely applied."""

    reason: str

    def __init__(self, reason: str) -> None:
        """Retain the actionable reason for transport error reporting."""
        self.reason = reason
        super().__init__(reason)


class InstallResult(BaseModel):
    """Report installed targets, changed files, and restorable backups."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    status: Literal["installed"] = "installed"
    tools: tuple[NotificationTool, ...]
    changed_paths: tuple[str, ...]
    backups: tuple[str, ...]
    warnings: tuple[str, ...] = ("Restart the selected agent to reload notification settings.",)


class Hook(BaseModel):
    """Parse command fields while preserving other Claude hook properties."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)
    type: str
    command: str | None = None


class HookGroup(BaseModel):
    """Retain matchers and non-command hook settings unchanged."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)
    hooks: tuple[Hook, ...]


class ClaudeSettings(BaseModel):
    """Validate only the modified hook structure; retain other settings."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)
    hooks: dict[str, tuple[HookGroup, ...]] = Field(default_factory=dict)


def _without_legacy(groups: tuple[HookGroup, ...]) -> tuple[HookGroup, ...]:
    retained: list[HookGroup] = []
    for group in groups:
        hooks: list[Hook] = []
        for hook in group.hooks:
            try:
                arguments = shlex.split(hook.command or "")
            except ValueError:
                arguments = []
            if hook.type != "command" or tuple(arguments) != LEGACY_COMMAND:
                hooks.append(hook)
        if hooks or not group.hooks:
            retained.append(group.model_copy(update={"hooks": tuple(hooks)}))
    return tuple(retained)


def _claude_change(config: Path, command: list[str]) -> Change:
    settings = ClaudeSettings.model_validate_json(config.read_bytes() if config.exists() else b"{}")
    shell_command = shlex.join(command)
    merged = {event: _without_legacy(groups) for event, groups in settings.hooks.items()}
    groups = merged.get("Stop", ())
    already_installed = any(
        hook.command == shell_command for group in groups for hook in group.hooks
    )
    if already_installed and merged == settings.hooks:
        return Change(config, config.read_bytes())
    if not already_installed:
        merged["Stop"] = (*groups, HookGroup(hooks=(Hook(type="command", command=shell_command),)))
    updated = settings.model_copy(update={"hooks": merged})
    return Change(config, (updated.model_dump_json(indent=2, exclude_unset=True) + "\n").encode())


def _codex_changes(config: Path, command: list[str], install_dir: Path) -> tuple[Change, ...]:
    document = parse(config.read_text(encoding="utf-8") if config.exists() else "")
    previous: list[str] = []
    if "notify" in document:
        previous = TypeAdapter(list[str]).validate_python(document["notify"])
    if tuple(previous) == LEGACY_COMMAND:
        previous = []
    if previous[1:] == command[1:]:
        document["notify"] = command
        return (Change(config, dumps(document).encode()),)
    document["notify"] = command
    return (
        Change(install_dir / "previous-notify.txt", shlex.join(previous).encode()),
        Change(config, dumps(document).encode()),
    )


def _read_sound(sound_path: Path) -> bytes:
    if sound_path.stat().st_size > MAX_SOUND_BYTES:
        raise NotificationInstallError(reason="Notification WAV exceeds 32 MB.")
    with wave.open(str(sound_path), "rb") as sound:
        frames = sound.readframes(sound.getnframes())
        expected = sound.getnframes() * sound.getnchannels() * sound.getsampwidth()
        if not frames or len(frames) != expected:
            raise NotificationInstallError(reason="Notification WAV is empty or truncated.")
    return sound_path.read_bytes()


def install_notifications(
    sound_path: Path,
    tools: tuple[NotificationTool, ...],
    *,
    claude_dir: Path | None = None,
    codex_dir: Path | None = None,
) -> InstallResult:
    """Atomically install a completion WAV while preserving unrelated hooks."""
    if not tools or len(set(tools)) != len(tools):
        raise NotificationInstallError(reason="Select one or both distinct notification tools.")
    claude = claude_dir or Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    codex = codex_dir or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    homes = {"claude": claude.resolve(), "codex": codex.resolve()}
    try:
        sound_bytes = _read_sound(sound_path)
        player_bytes = Path(__file__).with_name("notification_player.py").read_bytes()
        with locked_directories(tuple(homes[tool] for tool in tools)):
            changes: list[Change] = []
            configs: set[Path] = set()
            for tool in tools:
                install_dir = homes[tool] / "voice-of-karina"
                player = install_dir / "player.py"
                command = [str(Path(sys.executable).resolve()), str(player), tool]
                changes.extend(
                    (
                        Change(player, player_bytes),
                        Change(install_dir / "complete.wav", sound_bytes),
                    )
                )
                match tool:
                    case "claude":
                        config = homes[tool] / "settings.json"
                        changes.append(_claude_change(config, command))
                    case "codex":
                        config = homes[tool] / "config.toml"
                        changes.extend(_codex_changes(config, command, install_dir))
                    case unreachable:
                        assert_never(unreachable)
                configs.add(config)
            changed, backups = commit_changes(tuple(changes), frozenset(configs))
    except (OSError, EOFError, wave.Error, ValidationError, ParseError) as exc:
        raise NotificationInstallError(reason=f"Notification installation failed: {exc}") from exc
    return InstallResult(tools=tools, changed_paths=changed, backups=backups)
