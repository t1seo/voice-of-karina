# Karina Voice Notification Generator

**AI 음성 복제 도구** — YouTube 영상의 목소리로 Claude Code 맞춤 알림음을 생성합니다. Qwen3-TTS, Whisper, Demucs 기반.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/platform-Linux%20|%20macOS-lightgrey" alt="Platform">
  <img src="https://img.shields.io/badge/GPU-CUDA%2012.0%2B%20|%20Apple%20Silicon-green" alt="GPU">
  <img src="https://img.shields.io/badge/TTS-Qwen3--TTS%201.7B-orange" alt="TTS Model">
  <img src="https://img.shields.io/badge/license-MIT-brightgreen" alt="License">
  <a href="README.md"><img src="https://img.shields.io/badge/README-English-blue" alt="English"></a>
</p>

<p align="center">
  <img src="assets/karina.jpg" alt="Karina" width="800">
</p>

## 📖 이게 뭔가요?

Claude Code는 당신의 주의가 필요할 때 알림음을 재생합니다 — 권한 요청, 작업 완료 등.
이 도구는 그 **알림음을 원하는 사람의 목소리로 바꿔줍니다.** YouTube 클립 하나로 목소리를 복제하죠.

YouTube URL(인터뷰, 방송, 팟캐스트)을 넣고 깨끗한 몇 초의 발화를 고르면, 그 사람 목소리로
한국어·영어 알림 문장 세트를 생성합니다 — 명령어 하나로 Claude Code에 바로 연결할 수 있어요.
모든 처리는 **로컬**(Apple Silicon 또는 NVIDIA GPU)에서 이뤄지며, 음성은 컴퓨터 밖으로 나가지 않습니다.

먼저 들어보고 싶으신가요? 맨 아래 [🔊 목소리 샘플](#-목소리-샘플)로 가세요.

## 📦 요구 사항 & 설치

| 플랫폼 | 요구 사항 |
|--------|----------|
| **macOS** | Apple Silicon (M1+), 32GB+ RAM, [pixi](https://pixi.sh) |
| **Linux** | NVIDIA GPU, CUDA 12.0+, [pixi](https://pixi.sh) |

```bash
git clone https://github.com/t1seo/karina-voice-notification.git
cd karina-voice-notification

pixi install
pixi run install-deps-mac    # macOS (Apple Silicon)
pixi run install-deps-linux  # Linux (NVIDIA GPU)
```

## 🔄 작동 흐름

원본 YouTube 링크를 깨끗한 알림음으로 바꾸는 6단계 파이프라인입니다:

```mermaid
flowchart LR
    A([YouTube URL]) --> B[다운로드<br/>yt-dlp]
    B --> C[BGM 제거<br/>Demucs]
    C --> D[분할 &amp; 선택<br/>깨끗한 구간]
    D --> E[전사<br/>Whisper large-v3]
    E --> F[음성 복제<br/>Qwen3-TTS 1.7B]
    F --> G([알림음 .wav])
```

| 단계 | 기술 | 설명 |
|------|------|------|
| 다운로드 | yt-dlp | 최고 음질 오디오 추출 |
| BGM 제거 | Demucs (Meta AI) | 선택 사항 — 배경음악을 제거해 더 깨끗한 레퍼런스 확보 |
| 분할 & 선택 | pydub | 구간으로 자르고 5~15초 깨끗한 발화 선택 |
| 전사 | Whisper large-v3 | mlx-whisper (Mac) / faster-whisper (Linux) |
| 음성 복제 | Qwen3-TTS 1.7B | 교차언어 지원 — 한국어 레퍼런스로 영어도 발화 가능 |

**Claude Code와 Codex 양쪽 지원** — 같은 스킬, 같은 사운드.

### 1. 알림음 생성

**대화형 (권장)** — Claude Code나 Codex에서 스킬을 실행하면 대화로 안내합니다:
YouTube 링크 붙여넣기 → 각 알림 문구 정하기 → 완료.

```
/generate-voice
```

**또는 CLI로:**

```bash
pixi run pipeline          # 메뉴 방식: URL → 세그먼트 선택 → 생성
# 또는 비대화형 원샷:
pixi run quickstart "https://youtu.be/VIDEO_ID" --line "idle_prompt:다 됐어요!"
```

어느 방식이든 알림 세트는 `output/notifications/`에 생성됩니다.

### 2. Claude Code / Codex에 설치

두 도구 중 어디서든 설정 스킬을 실행:

```
/setup-notifications
```

또는 설치기를 직접 실행:

```bash
pixi run install-notifications          # 양쪽 자동 감지
python scripts/install_notifications.py --tool codex   # Codex만
python scripts/install_notifications.py --dry-run      # 변경 미리보기
```

사운드를 복사하고 이벤트를 연결합니다 — **Claude Code**: `~/.claude/settings.json`에
`Stop` + `Notification` 훅, **Codex**: `~/.codex/config.toml`에 `notify` 프로그램
(턴 완료 시 발동) + `~/.codex/skills/`에 스킬 복사. 편집 파일은 모두 백업되며,
재실행해도 안전합니다. 설치 후 도구를 재시작하면 훅이 로드됩니다.

### 💡 좋은 결과를 위한 팁

**좋은 음성 소스**
- 인터뷰, 단독 발화, 팟캐스트
- 뮤직비디오는 **BGM 제거** 활성화

**피해야 할 것**
- 시끄러운 환경, 여러 명이 말하는 영상
- 5초 미만의 짧은 클립

### 🎨 커스터마이징

`notification_lines.json`을 수정하여 알림 문구를 변경합니다:

```json
{"text": "원하는 문구를 여기에", "filename": "permission_prompt_1.wav"}
```

### 🛠️ 문제 해결

| 문제 | 해결 방법 |
|------|----------|
| 음성 품질 저하 | 더 깨끗한 소스 사용, BGM 제거 활성화 |
| Hook 소리 안남 | `~/.claude/sounds/` 확인, 권한 확인 |
| 의존성 오류 | `pixi run install-deps-mac` 또는 `install-deps-linux` 실행 |
| YouTube 다운로드 실패 (HTTP 403) | yt-dlp 업데이트: `pixi run pip install -U yt-dlp` |

## 🔊 목소리 샘플

카리나 목소리로 Qwen3-TTS가 복제한 한국어 알림 문구 세 개 ([인터뷰 출처](https://www.youtube.com/watch?v=r96zEiIHVf4)). ▶ 를 누르면 재생됩니다:

**작업 완료** — *작업을 완료했습니다.*

https://github.com/user-attachments/assets/4414a9c8-8430-459f-88c7-e88460971a8e

**권한 요청** — *실행 허가가 필요합니다.*

https://github.com/user-attachments/assets/5d1de7c1-bf1d-45ed-8b78-0525ecb2ebc1

**인증 성공** — *인증에 성공했습니다.*

https://github.com/user-attachments/assets/2440c136-482a-4281-919c-b06f43ae44a1

> 플레이어는 파형 비디오라 GitHub에서 인라인 재생됩니다. 원본 `.wav`는 [`assets/samples/`](assets/samples)에 있고 `pixi run samples`로 재생성합니다.

## 라이선스

MIT License
