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

> YouTube 영상에서 **원하는 목소리**로 Claude Code 알림음을 생성합니다.

## 🔊 목소리 샘플

카리나 목소리로 복제한 알림음 세 종류 ([인터뷰 출처](https://www.youtube.com/watch?v=r96zEiIHVf4)). ▶ 를 클릭하면 재생됩니다:

| 알림 | 문장 | 샘플 |
|------|------|------|
| **작업 완료** | 다 끝났어요! 결과 한번 확인해주세요~ | ▶ [듣기](assets/samples/karina_done_ko.wav) |
| **권한 요청** | 잠깐만요! 이거 실행해도 괜찮을까요? 허락해주세요~ | ▶ [듣기](assets/samples/karina_permission_ko.wav) |
| **인증 성공** | 인증이 완료되었어요! 도와주셔서 정말 고마워요~ | ▶ [듣기](assets/samples/karina_auth_ko.wav) |

> `pixi run samples`로 직접 생성할 수 있습니다. 클릭하면 브라우저 오디오 플레이어에서 `.wav`가 재생됩니다.

## 빠른 시작

### 1. 설치

```bash
git clone https://github.com/t1seo/karina-voice-notification.git
cd karina-voice-notification

pixi install
pixi run install-deps-mac    # macOS (Apple Silicon)
pixi run install-deps-linux  # Linux (NVIDIA GPU)
```

### 2. 실행

```bash
pixi run pipeline
```

인터랙티브 메뉴를 따라:
1. 깨끗한 음성이 있는 YouTube URL 입력
2. 깨끗한 음성 구간 선택 (5-15초)
3. 알림음 생성

### 3. Claude Code에 적용

Claude Code에서 실행:

```
/setup-notifications
```

이 스킬이 자동으로 음성 파일을 복사하고 Hook을 설정합니다.

## 요구 사항

| 플랫폼 | 요구 사항 |
|--------|----------|
| **macOS** | Apple Silicon (M1+), 32GB+ RAM, [pixi](https://pixi.sh) |
| **Linux** | NVIDIA GPU, CUDA 12.0+, [pixi](https://pixi.sh) |

## 좋은 결과를 위한 팁

**좋은 음성 소스:**
- 인터뷰, 단독 발화, 팟캐스트
- 뮤직비디오는 "배경음악 제거" 활성화

**피해야 할 것:**
- 시끄러운 환경, 여러 명이 말하는 영상
- 5초 미만의 짧은 클립

## 커스터마이징

`notification_lines.json`을 수정하여 알림 문구 변경:

```json
{"text": "원하는 문구를 여기에", "filename": "permission_prompt_1.wav"}
```

## 작동 원리

```
YouTube → 다운로드 → [BGM 제거] → 분할 → 선택 → 전사 → 음성 복제 → 출력
```

| 단계 | 기술 |
|------|------|
| 다운로드 | yt-dlp |
| BGM 제거 | Demucs (Meta AI) |
| 전사 | Whisper large-v3 |
| 음성 복제 | Qwen3-TTS 1.7B |

## 문제 해결

| 문제 | 해결 방법 |
|------|----------|
| 음성 품질 저하 | 더 깨끗한 소스 사용, BGM 제거 활성화 |
| Hook 소리 안남 | `~/.claude/sounds/` 확인, 권한 확인 |
| 의존성 오류 | `pixi run install-deps-mac` 또는 `install-deps-linux` 실행 |

## 라이선스

MIT License
