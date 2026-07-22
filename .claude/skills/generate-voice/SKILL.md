---
name: generate-voice
description: Generate Claude Code / Codex notification sounds in a cloned voice from a YouTube link, guiding the user conversationally. Use when the user wants to "make notification sounds", "clone a voice", "generate voice alerts from a YouTube link", or mentions the karina-voice-notification tool.
---

# Generate Voice Notifications (conversational)

Walk the user through creating notification sounds in a voice cloned from a
YouTube clip. **Drive this as a conversation** — collect the inputs one at a
time, confirm, then run the generator. Works the same in Claude Code and Codex.

## Step 1 — Locate the project

Run everything from the `karina-voice-notification` repository.
- If the current directory is that repo (has `src/quickstart.py`), use it.
- Otherwise ask the user where it is, or clone it:
  ```bash
  git clone https://github.com/t1seo/karina-voice-notification.git
  cd karina-voice-notification
  ```

## Step 2 — Make sure dependencies are installed

Check whether the environment is ready (once per machine):
```bash
pixi run python -c "import qwen_tts, torch" 2>/dev/null && echo READY || echo NEEDS_INSTALL
```
If `NEEDS_INSTALL`, install (this downloads several GB — tell the user it takes a while):
```bash
pixi install
pixi run install-deps-mac    # macOS (Apple Silicon)
pixi run install-deps-linux  # Linux (NVIDIA GPU)
```

## Step 3 — Ask for the voice source (conversation)

Ask the user:
> "Which YouTube video should I clone the voice from? Paste the link.
> A clean interview or solo-talking clip works best."

Store it as `<URL>`.

## Step 3.5 — Ask about background-music removal (conversation)

The pipeline can strip background music/noise from the source with Demucs before
cloning. Ask the user which they want:

> "Should I remove the background music first? By default I do — it usually gives
> a cleaner, more natural clone. Keep it **on** for music videos or noisy clips.
> Turn it **off** only if the source is already clean solo speech (it's faster,
> and occasionally preserves more of the original tone)."

- **Remove BGM (default)** → no extra flag.
- **Keep BGM / skip removal** → add `--no-bgm-removal` in Step 5.

If unsure, recommend removal (on).

## Step 4 — Ask what the notifications should say (conversation)

Explain the notification types and ask what they want each to say. Offer the
current defaults (from `notification_lines.json`) and let them customize:

| Type | When it plays | Default |
|------|---------------|---------|
| `idle_prompt` | Task finished | 다 끝났어요! 결과 한번 확인해주세요~ |
| `permission_prompt` | Permission needed | 잠깐만요! 이거 실행해도 괜찮을까요? |
| `auth_success` | Auth succeeded | 인증이 완료되었어요! |
| `elicitation_dialog` | Input needed | 여기에 입력이 필요해요! |

Ask something like:
> "What should each alert say? You can keep the defaults, tweak the wording, or
> give me your own lines. Which language — Korean or English?"

Collect the user's choices. Keep the defaults for anything they don't change.

## Step 5 — Generate

Pass the user's choices as `--line TYPE:TEXT` overrides (one per customized line):
```bash
pixi run python src/quickstart.py "<URL>" \
    --line "idle_prompt:<their text>" \
    --line "permission_prompt:<their text>" \
    --language korean
```
- Add `--language english` if they chose English.
- Add `--no-bgm-removal` if they chose to keep the background music in Step 3.5.
- Pick the engine with `--backend` (default `chatterbox`; also `qwen3`, `indextts2`, `cosyvoice`).
- Omit `--line` entirely to use all defaults.

This downloads the audio, removes BGM, auto-picks a clean segment, transcribes
it, and clones every line into `output/notifications/`. Report the generated
files when done, and let the user preview one (`afplay output/notifications/idle_prompt/idle_prompt_1.wav` on macOS).

## Step 6 — Offer to install

Ask if they want to hear these in Claude Code / Codex now. If yes, invoke the
**setup-notifications** skill (or run `python scripts/install_notifications.py`).
