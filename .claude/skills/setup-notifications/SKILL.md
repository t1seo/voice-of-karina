---
name: setup-notifications
description: Install voice notification sounds into Claude Code and/or Codex — copies the generated sounds and wires up the event hooks. Use when the user asks to "install notifications", "setup sounds", "configure voice alerts", or "make the notification sounds play in Claude Code / Codex".
---

# Setup Voice Notifications (Claude Code + Codex)

Install the generated sounds so they actually play when Claude Code or Codex
needs attention. One installer handles both tools.

## Prerequisite

Sounds must exist in `output/notifications/` first. If it's empty, run the
**generate-voice** skill (or `pixi run pipeline`) to create them.

## Recommended: use the installer

From the repo root:

```bash
python scripts/install_notifications.py            # both tools (auto-detects)
python scripts/install_notifications.py --tool claude
python scripts/install_notifications.py --tool codex
python scripts/install_notifications.py --dry-run  # preview changes
```

The installer:
- copies a cross-platform player to `~/.local/share/voice-notification/`
- copies the sounds to `~/.claude/sounds/` and/or `~/.codex/sounds/`
- **Claude Code:** adds `Stop` + `Notification` hooks to `~/.claude/settings.json`
- **Codex:** sets a `notify` program in `~/.codex/config.toml` (fires on
  `agent-turn-complete`) and copies the skills to `~/.codex/skills/`
- backs up every file it edits (`*.bak-voicenotif-<timestamp>`) and is safe to
  re-run (idempotent)

After it finishes, **restart Claude Code / Codex** (or start a new session) so
the hooks load.

## What gets wired up

| Tool | Event | Sound |
|------|-------|-------|
| Claude Code | `Stop` | `idle_prompt_*` (task complete) |
| Claude Code | `Notification` | matching type (`permission_prompt_*`, ...) |
| Codex | `agent-turn-complete` | `idle_prompt_*` (turn finished) |

## Verify

- Claude Code: finish any task — you should hear the completion sound.
- Codex: run a short `codex exec "say hi"` — the sound should play when it finishes.
- Player debug log: `~/.cache/voice-notification/hook_debug.log`
- Sounds present: `ls ~/.claude/sounds/` and/or `ls ~/.codex/sounds/`

## Platform notes

The player auto-detects the audio command: `afplay` (macOS), or `paplay` /
`aplay` / `ffplay` (Linux). No manual edits needed.

## Manual setup (reference)

If you prefer to wire it by hand instead of the installer:

**Claude Code** — add to `~/.claude/settings.json`:
```json
{
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "python3 ~/.local/share/voice-notification/notification_player.py", "timeout": 10}]}],
    "Notification": [{"hooks": [{"type": "command", "command": "python3 ~/.local/share/voice-notification/notification_player.py", "timeout": 10}]}]
  }
}
```

**Codex** — add to `~/.codex/config.toml`:
```toml
notify = ["python3", "/Users/you/.local/share/voice-notification/notification_player.py"]
```
