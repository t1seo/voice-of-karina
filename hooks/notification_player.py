#!/usr/bin/env python3
"""Cross-tool, cross-platform notification sound player.

One script, two input channels:

* **Claude Code hook** — the event arrives as JSON on **stdin** with a
  `hook_event_name` field (`Stop`, `Notification`, ...).
* **Codex `notify`** — the event arrives as JSON in **argv[1]** with a
  `type` field (`agent-turn-complete`, ...).

Either way we map the event to a notification category and play a random
matching `*.wav` from the sounds directory. Playback is fire-and-forget so
the agent is never blocked.

Sounds directory resolution order:
  1. $VOICE_NOTIFICATION_SOUNDS
  2. ~/.claude/sounds   (Claude default)
  3. ~/.codex/sounds    (Codex default)
"""

import glob
import json
import os
import platform
import random
import shutil
import subprocess
import sys
from datetime import datetime

DEBUG_LOG = os.path.expanduser("~/.cache/voice-notification/hook_debug.log")


def debug_log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(DEBUG_LOG), exist_ok=True)
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def find_sounds_dir() -> str | None:
    candidates = [
        os.environ.get("VOICE_NOTIFICATION_SOUNDS"),
        os.path.expanduser("~/.claude/sounds"),
        os.path.expanduser("~/.codex/sounds"),
    ]
    for path in candidates:
        if path and os.path.isdir(path) and glob.glob(os.path.join(path, "*.wav")):
            return path
    return None


def pick_player() -> list[str] | None:
    """Return the audio-player command prefix for this platform, or None."""
    system = platform.system()
    if system == "Darwin" and shutil.which("afplay"):
        return ["afplay"]
    # Linux: try the common players in order of ubiquity
    for cmd in ("paplay", "aplay", "ffplay"):
        if shutil.which(cmd):
            return [cmd, "-nodisp", "-autoexit"] if cmd == "ffplay" else [cmd]
    return None


def play(notification_type: str) -> None:
    sounds_dir = find_sounds_dir()
    if not sounds_dir:
        debug_log("No sounds directory found")
        return

    matches = glob.glob(os.path.join(sounds_dir, f"{notification_type}_*.wav"))
    if not matches:
        # Fall back to any completion sound so the user still hears *something*
        matches = glob.glob(os.path.join(sounds_dir, "idle_prompt_*.wav"))
    if not matches:
        debug_log(f"No sound for '{notification_type}' in {sounds_dir}")
        return

    player = pick_player()
    if not player:
        debug_log("No audio player available (afplay/paplay/aplay/ffplay)")
        return

    sound = random.choice(matches)
    try:
        subprocess.Popen(
            player + [sound],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach so playback survives hook exit
        )
        debug_log(f"Playing {notification_type}: {sound}")
    except Exception as e:
        debug_log(f"Playback error: {e}")


# Map raw event names (from either tool) to our notification categories.
CODEX_EVENT_TO_TYPE = {
    "agent-turn-complete": "idle_prompt",
}
CLAUDE_EVENT_TO_TYPE = {
    "Stop": "idle_prompt",
    "SubagentStop": "idle_prompt",
}


def handle_codex(argv_json: str) -> None:
    """Codex `notify`: JSON passed as argv[1]."""
    try:
        data = json.loads(argv_json)
    except json.JSONDecodeError as e:
        debug_log(f"Codex JSON decode error: {e}")
        return
    event = data.get("type", "")
    debug_log(f"Codex event: {event}")
    play(CODEX_EVENT_TO_TYPE.get(event, "idle_prompt" if event else ""))


def handle_claude(stdin_data: str) -> None:
    """Claude Code hook: JSON on stdin."""
    try:
        data = json.loads(stdin_data) if stdin_data else {}
    except json.JSONDecodeError as e:
        debug_log(f"Claude JSON decode error: {e}")
        print(json.dumps({"continue": True}))
        return

    event = data.get("hook_event_name", "")
    debug_log(f"Claude event: {event}")
    if event == "Notification":
        # Claude's Notification payload names the specific kind
        play(data.get("notification_type", "permission_prompt"))
    else:
        play(CLAUDE_EVENT_TO_TYPE.get(event, "idle_prompt"))
    print(json.dumps({"continue": True}))


def main() -> None:
    debug_log("=== player invoked ===")
    # Codex passes the event JSON as a CLI argument; Claude pipes it on stdin.
    if len(sys.argv) > 1 and sys.argv[1].strip().startswith("{"):
        handle_codex(sys.argv[1])
    else:
        handle_claude(sys.stdin.read() if not sys.stdin.isatty() else "")


if __name__ == "__main__":
    main()
