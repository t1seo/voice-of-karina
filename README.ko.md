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

## 🚀 사용법

### 1. 파이프라인 실행

```bash
pixi run pipeline
```

인터랙티브 메뉴를 따라:

1. 깨끗한 음성이 있는 YouTube URL 입력
2. 깨끗한 음성 구간 선택 (5~15초)
3. 알림음 생성 → `output/notifications/`

### 2. Claude Code에 연결

Claude Code에서 실행:

```
/setup-notifications
```

이 스킬이 음성 파일을 `~/.claude/sounds/`에 복사하고 Hook을 자동 설정합니다.

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

카리나 목소리로 복제한 알림음 세 종류 ([인터뷰 출처](https://www.youtube.com/watch?v=r96zEiIHVf4)). ▶ 를 누르면 바로 재생됩니다:

**작업 완료** — *다 끝났어요! 결과 한번 확인해주세요~*

https://github.com/user-attachments/assets/25a5c321-327e-4e1a-a7b4-28807d1feddc

**권한 요청** — *잠깐만요! 이거 실행해도 괜찮을까요? 허락해주세요~*

https://github.com/user-attachments/assets/2342c4e3-4be2-4067-a94d-8bf38417f739

**인증 성공** — *인증이 완료되었어요! 도와주셔서 정말 고마워요~*

https://github.com/user-attachments/assets/da276adb-389b-4b31-b583-720123f40cf7

> 위 플레이어는 GitHub에서 인라인 재생되도록 파형 비디오로 만들었습니다. 원본 `.wav`는 [`assets/samples/`](assets/samples)에 있고 `pixi run samples`로 재생성할 수 있습니다.

## 라이선스

MIT License
