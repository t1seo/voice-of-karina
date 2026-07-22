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

> Generate Claude Code notification sounds with **any voice** from YouTube videos.

## 🔊 Voice Samples

Three interview voices — **Karina** ([source](https://www.youtube.com/watch?v=r96zEiIHVf4)), **Lee Chae-young** ([source](https://www.youtube.com/watch?v=8amfOc9d02I)), **Park Eun-bin** ([source](https://www.youtube.com/watch?v=1UcsKU7gY2k)) — each cloned across 3 notification lines in both Korean and English (18 clips). Click ▶ to listen:

**Case 1 — Task complete** · *"다 끝났어요! 결과 한번 확인해주세요~"* / *"All done! Please take a look at the results."*

| Voice | 🇰🇷 Korean | 🇺🇸 English |
|-------|-----------|-----------|
| 🎤 **Karina** | ▶ [Listen](assets/samples/karina_done_ko.wav) | ▶ [Listen](assets/samples/karina_done_en.wav) |
| 🎤 **Lee Chae-young** | ▶ [Listen](assets/samples/chaeyoung_done_ko.wav) | ▶ [Listen](assets/samples/chaeyoung_done_en.wav) |
| 🎤 **Park Eun-bin** | ▶ [Listen](assets/samples/eunbin_done_ko.wav) | ▶ [Listen](assets/samples/eunbin_done_en.wav) |

**Case 2 — Permission request** · *"잠깐만요! 이거 실행해도 괜찮을까요? 허락해주세요~"* / *"Wait a second! Is it okay to run this? Please allow it."*

| Voice | 🇰🇷 Korean | 🇺🇸 English |
|-------|-----------|-----------|
| 🎤 **Karina** | ▶ [Listen](assets/samples/karina_permission_ko.wav) | ▶ [Listen](assets/samples/karina_permission_en.wav) |
| 🎤 **Lee Chae-young** | ▶ [Listen](assets/samples/chaeyoung_permission_ko.wav) | ▶ [Listen](assets/samples/chaeyoung_permission_en.wav) |
| 🎤 **Park Eun-bin** | ▶ [Listen](assets/samples/eunbin_permission_ko.wav) | ▶ [Listen](assets/samples/eunbin_permission_en.wav) |

**Case 3 — Auth success** · *"인증이 완료되었어요! 도와주셔서 정말 고마워요~"* / *"Authentication complete! Thank you so much for your help."*

| Voice | 🇰🇷 Korean | 🇺🇸 English |
|-------|-----------|-----------|
| 🎤 **Karina** | ▶ [Listen](assets/samples/karina_auth_ko.wav) | ▶ [Listen](assets/samples/karina_auth_en.wav) |
| 🎤 **Lee Chae-young** | ▶ [Listen](assets/samples/chaeyoung_auth_ko.wav) | ▶ [Listen](assets/samples/chaeyoung_auth_en.wav) |
| 🎤 **Park Eun-bin** | ▶ [Listen](assets/samples/eunbin_auth_ko.wav) | ▶ [Listen](assets/samples/eunbin_auth_en.wav) |

> Generate these yourself with `pixi run samples`. Clicking a link opens the `.wav` in your browser's audio player.

## Quick Start

### 1. Install

```bash
git clone https://github.com/t1seo/karina-voice-notification.git
cd karina-voice-notification

pixi install
pixi run install-deps-mac    # macOS (Apple Silicon)
pixi run install-deps-linux  # Linux (NVIDIA GPU)
```

### 2. Run

```bash
pixi run pipeline
```

Follow the interactive menu to:
1. Paste a YouTube URL with clear voice
2. Select a clean voice segment (5-15 seconds)
3. Generate notification sounds

### 3. Use with Claude Code

In Claude Code, run:

```
/setup-notifications
```

This skill automatically copies sounds and configures hooks for you.

## Requirements

| Platform | Requirements |
|----------|-------------|
| **macOS** | Apple Silicon (M1+), 32GB+ RAM, [pixi](https://pixi.sh) |
| **Linux** | NVIDIA GPU, CUDA 12.0+, [pixi](https://pixi.sh) |

## Tips for Best Results

**Good voice sources:**
- Interview clips, solo speaking, podcasts
- Enable "BGM Removal" for music videos

**Avoid:**
- Noisy environments, multiple speakers
- Clips shorter than 5 seconds

## Customization

Edit `notification_lines.json` to change notification phrases:

```json
{"text": "Your custom phrase here", "filename": "permission_prompt_1.wav"}
```

## How It Works

```
YouTube → Download → [BGM Removal] → Split → Select → Transcribe → Voice Clone → Output
```

| Step | Technology |
|------|------------|
| Download | yt-dlp |
| BGM Removal | Demucs (Meta AI) |
| Transcription | Whisper large-v3 |
| Voice Cloning | Qwen3-TTS 1.7B |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Poor voice quality | Use cleaner source, enable BGM Removal |
| Hook not playing | Check `~/.claude/sounds/` exists, verify permissions |
| Missing dependencies | Run `pixi run install-deps-mac` or `install-deps-linux` |

## License

MIT License
