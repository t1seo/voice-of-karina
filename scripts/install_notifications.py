#!/usr/bin/env python3
"""Install voice notification sounds into Claude Code and/or Codex.

Sets up, for each detected tool:
  * a shared, cross-platform player script
  * the generated sounds (output/notifications/)
  * the event wiring:
      - Claude Code: `Stop` + `Notification` hooks in ~/.claude/settings.json
      - Codex:       `notify` program in ~/.codex/config.toml (fires on
                     agent-turn-complete)
  * the repo skills copied into ~/.codex/skills/ (Codex has no per-project
    skill discovery, so its skills live globally)

Pure standard library — safe to run with the system `python3`, outside pixi.
Every file it edits is backed up first (`<file>.bak-voicenotif-<timestamp>`).

Usage:
    python scripts/install_notifications.py                # both tools, auto-detect
    python scripts/install_notifications.py --tool claude  # just Claude Code
    python scripts/install_notifications.py --tool codex   # just Codex
    python scripts/install_notifications.py --dry-run      # show what would change
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
REPO_PLAYER = PROJECT_ROOT / "hooks" / "notification_player.py"
DEFAULT_SOUNDS = PROJECT_ROOT / "output" / "notifications"
REPO_SKILLS = PROJECT_ROOT / ".claude" / "skills"
SKILLS_TO_INSTALL = ["generate-voice", "setup-notifications"]

HOME = Path.home()
SHARED_PLAYER = HOME / ".local" / "share" / "voice-notification" / "notification_player.py"
CLAUDE_DIR = HOME / ".claude"
CODEX_DIR = HOME / ".codex"

STAMP = time.strftime("%Y%m%d-%H%M%S")


def backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.bak-voicenotif-{STAMP}"))


def collect_sounds(sounds_dir: Path) -> list[Path]:
    return sorted(sounds_dir.rglob("*.wav"))


def install_shared_player(dry: bool) -> None:
    print(f"  player  -> {SHARED_PLAYER}")
    if dry:
        return
    SHARED_PLAYER.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_PLAYER, SHARED_PLAYER)
    SHARED_PLAYER.chmod(0o755)


def copy_sounds(sounds: list[Path], dest: Path, dry: bool) -> None:
    print(f"  sounds  -> {dest}  ({len(sounds)} files)")
    if dry:
        return
    dest.mkdir(parents=True, exist_ok=True)
    for wav in sounds:
        shutil.copy2(wav, dest / wav.name)


# ----------------------------- Claude Code -----------------------------

def setup_claude(sounds: list[Path], dry: bool) -> None:
    print("\n[Claude Code]")
    copy_sounds(sounds, CLAUDE_DIR / "sounds", dry)

    settings_path = CLAUDE_DIR / "settings.json"
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  !! {settings_path} is not valid JSON — leaving it untouched.")
            return

    command = f'python3 "{SHARED_PLAYER}"'
    hook_entry = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
    hooks = settings.setdefault("hooks", {})

    changed = False
    for event in ("Stop", "Notification"):
        existing = hooks.setdefault(event, [])
        if any(command in json.dumps(e) for e in existing):
            print(f"  hook    = {event}: already present, skipping")
        else:
            existing.append(hook_entry)
            print(f"  hook    + {event}: {command}")
            changed = True

    if changed and not dry:
        backup(settings_path)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  wrote   {settings_path}")


# ------------------------------- Codex ---------------------------------

def setup_codex(sounds: list[Path], dry: bool) -> None:
    print("\n[Codex]")
    copy_sounds(sounds, CODEX_DIR / "sounds", dry)

    # notify program: fires on agent-turn-complete with a JSON argv
    config_path = CODEX_DIR / "config.toml"
    notify_line = f'notify = ["python3", "{SHARED_PLAYER}"]'
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        has_notify = any(ln.lstrip().startswith("notify") and "=" in ln.split("#")[0] for ln in lines)
        if has_notify:
            print("  notify  = already set in config.toml — leaving it untouched.")
        else:
            # Insert before the first [table] header so it stays top-level.
            idx = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("[")), len(lines))
            block = ["# voice-notification: play a sound when Codex finishes a turn", notify_line, ""]
            new_lines = lines[:idx] + block + lines[idx:]
            print(f"  notify  + {notify_line}")
            if not dry:
                backup(config_path)
                config_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                print(f"  wrote   {config_path}")
    else:
        print(f"  notify  + creating {config_path}")
        if not dry:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                "# voice-notification: play a sound when Codex finishes a turn\n"
                f"{notify_line}\n", encoding="utf-8")

    # Skills live globally for Codex
    dest_skills = CODEX_DIR / "skills"
    for name in SKILLS_TO_INSTALL:
        src = REPO_SKILLS / name
        if not src.is_dir():
            continue
        print(f"  skill   -> {dest_skills / name}")
        if not dry:
            dest = dest_skills / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install voice notifications for Claude Code / Codex")
    parser.add_argument("--tool", choices=["claude", "codex", "both"], default="both")
    parser.add_argument("--sounds", type=Path, default=DEFAULT_SOUNDS,
                        help="Directory of generated .wav sounds (default: output/notifications)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    sounds = collect_sounds(args.sounds)
    if not sounds:
        print(f"No .wav sounds found in {args.sounds}.")
        print("Generate some first (e.g. `pixi run pipeline` or the generate-voice skill).")
        sys.exit(1)

    want_claude = args.tool in ("claude", "both")
    want_codex = args.tool in ("codex", "both")
    # Auto-skip a tool that clearly isn't installed when the user asked for "both".
    if args.tool == "both":
        if not CLAUDE_DIR.exists():
            print("(~/.claude not found — skipping Claude Code)")
            want_claude = False
        if not CODEX_DIR.exists() and not shutil.which("codex"):
            print("(Codex not found — skipping Codex)")
            want_codex = False

    print(f"Installing {len(sounds)} sounds"
          f"{' [dry-run]' if args.dry_run else ''}"
          f"  tools: {'claude ' if want_claude else ''}{'codex' if want_codex else ''}".rstrip())
    install_shared_player(args.dry_run)
    if want_claude:
        setup_claude(sounds, args.dry_run)
    if want_codex:
        setup_codex(sounds, args.dry_run)

    print("\nDone." if not args.dry_run else "\nDry-run complete — no files changed.")
    if not args.dry_run:
        print("Restart Claude Code / Codex (or start a new session) to load the hooks.")


if __name__ == "__main__":
    main()
