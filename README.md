# Karina Voice Notification Generator

**AI Voice Cloning Tool** — Create custom notification sounds for Claude Code using any voice from YouTube videos. Powered by Qwen3-TTS, Whisper, and Demucs.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/platform-Linux%20|%20macOS-lightgrey" alt="Platform">
  <img src="https://img.shields.io/badge/GPU-CUDA%2012.0%2B%20|%20Apple%20Silicon-green" alt="GPU">
  <img src="https://img.shields.io/badge/TTS-Qwen3--TTS%201.7B-orange" alt="TTS Model">
  <img src="https://img.shields.io/badge/license-MIT-brightgreen" alt="License">
  <a href="README.ko.md"><img src="https://img.shields.io/badge/README-한국어-red" alt="Korean"></a>
</p>

<p align="center">
  <img src="assets/karina.jpg" alt="Karina" width="800">
</p>

## 📖 What is this?

Claude Code plays a notification sound when it needs your attention — a permission
prompt, a finished task, and so on. This tool lets you **replace those sounds with a
cloned voice of your choice**, cloned from any YouTube clip.

Point it at a YouTube URL (an interview, a stream, a podcast), pick a clean few
seconds of speech, and it generates a full set of Korean/English notification lines
in that person's voice — ready to wire into Claude Code with one command. Everything
runs **locally** on your machine (Apple Silicon or an NVIDIA GPU); no audio ever
leaves your computer.

Prefer to just hear it first? Jump to the [🔊 Voice Samples](#-voice-samples) at the bottom.

## 📦 Requirements & Installation

| Platform | Requirements |
|----------|-------------|
| **macOS** | Apple Silicon (M1+), 32GB+ RAM, [pixi](https://pixi.sh) |
| **Linux** | NVIDIA GPU, CUDA 12.0+, [pixi](https://pixi.sh) |

```bash
git clone https://github.com/t1seo/karina-voice-notification.git
cd karina-voice-notification

pixi install
pixi run install-deps-mac    # macOS (Apple Silicon)
pixi run install-deps-linux  # Linux (NVIDIA GPU)
```

## 🔄 Workflow

The pipeline turns a raw YouTube link into clean voice notifications in six steps:

```mermaid
flowchart LR
    A([YouTube URL]) --> B[Download<br/>yt-dlp]
    B --> C[BGM Removal<br/>Demucs]
    C --> D[Split &amp; Select<br/>clean segment]
    D --> E[Transcribe<br/>Whisper large-v3]
    E --> F[Voice Clone<br/>Qwen3-TTS 1.7B]
    F --> G([Notification .wav])
```

| Step | Technology | Notes |
|------|------------|-------|
| Download | yt-dlp | Extracts best-quality audio |
| BGM Removal | Demucs (Meta AI) | Optional — strips background music for a cleaner reference |
| Split & Select | pydub | Cut into segments; pick 5–15s of clean speech |
| Transcription | Whisper large-v3 | mlx-whisper (Mac) / faster-whisper (Linux) |
| Voice Cloning | Qwen3-TTS 1.7B | Cross-lingual — a Korean reference can speak English too |

Works with both **Claude Code** and **Codex** — same skills, same sounds.

### 1. Generate the sounds

**Conversationally (recommended)** — in Claude Code or Codex, run the skill and it
walks you through it: paste a YouTube link, choose what each alert should say, done.

```
/generate-voice
```

**Or via the interactive CLI:**

```bash
pixi run pipeline          # menu-driven: URL → segment → generate
# or one-shot, non-interactive:
pixi run quickstart "https://youtu.be/VIDEO_ID" --line "idle_prompt:다 됐어요!"
```

Either way the notification set lands in `output/notifications/`.

### 2. Install into Claude Code / Codex

Run the setup skill in either tool:

```
/setup-notifications
```

Or run the installer directly:

```bash
pixi run install-notifications          # both tools (auto-detects)
python scripts/install_notifications.py --tool codex   # just Codex
python scripts/install_notifications.py --dry-run      # preview changes
```

It copies the sounds and wires up the events — **Claude Code**: `Stop` +
`Notification` hooks in `~/.claude/settings.json`; **Codex**: a `notify` program
in `~/.codex/config.toml` (fires on turn completion) plus the skills into
`~/.codex/skills/`. Every edited file is backed up, and it's safe to re-run.
Restart the tool afterward to load the hooks.

### 💡 Tips for best results

**Good voice sources**
- Interview clips, solo speaking, podcasts
- Enable **BGM Removal** for music videos

**Avoid**
- Noisy environments or multiple speakers
- Clips shorter than 5 seconds

### 🎨 Customization

Edit `notification_lines.json` to change the phrases:

```json
{"text": "Your custom phrase here", "filename": "permission_prompt_1.wav"}
```

### 🛠️ Troubleshooting

| Problem | Solution |
|---------|----------|
| Poor voice quality | Use a cleaner source, enable BGM Removal |
| Hook not playing | Check `~/.claude/sounds/` exists, verify permissions |
| Missing dependencies | Run `pixi run install-deps-mac` or `install-deps-linux` |
| YouTube download fails (HTTP 403) | Update yt-dlp: `pixi run pip install -U yt-dlp` |

## 🔊 Voice Samples

Three Korean notification lines, cloned in Karina's voice ([interview source](https://www.youtube.com/watch?v=r96zEiIHVf4)) with Qwen3-TTS. Press ▶ to play:

**Task complete** — *작업을 완료했습니다.*

https://github.com/user-attachments/assets/4414a9c8-8430-459f-88c7-e88460971a8e

**Permission required** — *실행 허가가 필요합니다.*

https://github.com/user-attachments/assets/5d1de7c1-bf1d-45ed-8b78-0525ecb2ebc1

**Authentication succeeded** — *인증에 성공했습니다.*

https://github.com/user-attachments/assets/2440c136-482a-4281-919c-b06f43ae44a1

> Players are waveform videos so they play inline on GitHub. Source `.wav` files are in [`assets/samples/`](assets/samples); regenerate with `pixi run samples`.

## License

MIT License
