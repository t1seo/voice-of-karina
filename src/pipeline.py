#!/usr/bin/env python3
"""
Automated pipeline for Karina voice notification generation.
Cross-platform: Linux (CUDA) / Mac (MPS) / CPU

Usage:
    python pipeline.py [--skip-download] [url]
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure src/ directory is in Python path for local imports
_src_dir = Path(__file__).parent.resolve()
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# Suppress transformers warnings
import warnings

import numpy as np
import readchar
import soundfile as sf
import torch
import transformers
from loguru import logger
from rich import box
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text

from device_utils import DeviceInfo, DeviceType, detect_device, print_device_info
from tts_backends import DEFAULT_BACKEND, available_backends, get_backend

transformers.logging.set_verbosity_error()
warnings.filterwarnings("ignore", message=".*pad_token_id.*")

# Setup
console = Console()
logger.remove()
logger.add(sys.stderr, format="<level>{level: <7}</level> | {message}")

# ============== Internationalization ==============

LANG = "en"  # Default language

TEXTS = {
    "en": {
        # Menu
        "menu_footer": "↑↓ Navigate  •  Enter Select  •  q Quit",
        "main_title": "Karina Voice Generator",
        "main_subtitle": "Generate Claude Code notifications with aespa Karina's voice",
        "menu_full": "🚀 Run Full Pipeline",
        "menu_full_desc": "Download → [BGM Remove] → Split → Transcribe → [Post-process] → TTS",
        "menu_download": "📥 Download & Extract Audio",
        "menu_download_desc": "Download → [BGM Remove] → Split → Select segment",
        "menu_transcribe": "📝 Start from Transcribe",
        "menu_transcribe_desc": "Existing audio → Transcribe → [Post-process] → TTS",
        "menu_generate": "🎤 Generate Only",
        "menu_generate_desc": "Existing transcript → [Post-process] → TTS",
        "menu_postprocess": "🔊 Post-process Only",
        "menu_postprocess_desc": "Enhance existing audio (denoise, EQ, normalize)",
        "menu_exit": "❌ Exit",
        "cancel": "❌ Cancel",
        # Post-processing
        "postprocess_title": "Post-processing",
        "postprocess_subtitle": "⚠️  Warning: May degrade voice quality. Skip recommended.",
        "postprocess_enable": "✅ Enable Post-processing",
        "postprocess_enable_desc": "Apply denoise, EQ, dynamics (may lose voice characteristics)",
        "postprocess_disable": "⏭️  Skip Post-processing (Recommended)",
        "postprocess_disable_desc": "Keep original TTS output - better voice quality",
        "postprocess_complete": "✨ Post-processing Complete!",
        "postprocess_files": "Processed {n} files",
        # Source separation
        "separate_title": "Source Separation",
        "separate_subtitle": "⚠️  Warning: BGM in reference audio degrades voice cloning quality",
        "separate_enable": "🎵 Enable BGM Removal (Recommended)",
        "separate_enable_desc": "Use Demucs AI to extract vocals - cleaner reference audio",
        "separate_disable": "⏭️  Skip BGM Removal",
        "separate_disable_desc": "Use original audio (only if already clean)",
        "separate_running": "Separating vocals from background music...",
        "separate_complete": "✅ Vocals extracted successfully",
        "separate_not_installed": "Demucs not installed. Run: pip install demucs",
        # Transcribe Language
        "transcribe_lang_title": "Transcribe Language",
        "transcribe_lang_subtitle": "Select the language of the audio to transcribe",
        # TTS Language
        "tts_lang_title": "TTS Language",
        "tts_lang_subtitle": "Select the language for generated voice",
        # Split mode
        "split_title": "Split Mode",
        "split_subtitle": "Choose how to split the audio",
        "split_auto": "🔄 Auto Split",
        "split_auto_desc": "Split at 30-second intervals",
        "split_manual": "⏱️  Manual Input",
        "split_manual_desc": "Enter start times manually",
        # Segment selection
        "segment_title": "Select Segment",
        "segment_subtitle": "Choose a clean voice segment (preview with afplay)",
        # Steps
        "step_device": "Device Environment Check",
        "step_download": "Step 1: Download Audio from YouTube",
        "step_split": "Step 2: Split Audio into Segments",
        "step_select": "Step 3: Select Clean Voice Segment",
        "step_transcribe": "Step 4: Transcribe Audio",
        "step_setup": "Step 5: Setup Qwen3-TTS 1.7B Model",
        "step_generate": "Step 6: Generate Notification Voice Lines",
        # Messages
        "total_duration": "Total duration",
        "enter_start_time": "Enter start time (seconds or MM:SS, empty to finish)",
        "start_time_prompt": "Start time: ",
        "invalid_format": "Invalid format. Example: 30, 1:30, 90.5",
        "out_of_range": "Out of range",
        "created_segments": "Created {n} segments",
        "preview_tip": "💡 Tip: Preview with 'afplay <path>' (Mac) or 'aplay <path>' (Linux)",
        "youtube_url_prompt": "Enter for default",
        "audio_ready": "✅ Audio Ready!",
        "pipeline_complete": "✨ Pipeline Complete!",
        "next_steps": "Next steps:",
        "next_step_1": "1. Review generated audio files",
        "next_step_2": "2. Copy best ones to ~/.claude/sounds/",
        "next_step_3": "3. Configure Claude Code notification hooks",
        "bye": "👋 Bye!",
        "cancelled": "Cancelled",
        "no_gpu_warning": "No GPU detected, using CPU (will be slow)",
        "cleanup_title": "Cleanup",
        "cleanup_confirm": "Delete existing output files?",
        "cleanup_yes": "🗑️  Yes, delete all",
        "cleanup_yes_desc": "Remove all existing .wav files in output/notifications",
        "cleanup_no": "⏭️  No, keep existing",
        "cleanup_no_desc": "Keep existing files (may overwrite some)",
        "cleanup_deleted": "Deleted {n} existing files",
        "cleanup_skipped": "Keeping existing files",
    },
    "ko": {
        # Menu
        "menu_footer": "↑↓ 이동  •  Enter 선택  •  q 종료",
        "main_title": "Karina Voice Generator",
        "main_subtitle": "aespa 카리나 음성으로 Claude Code 알림음 생성",
        "menu_full": "🚀 전체 파이프라인 실행",
        "menu_full_desc": "다운로드 → [BGM 제거] → 분할 → 전사 → [후처리] → TTS",
        "menu_download": "📥 음성 다운로드 & 추출",
        "menu_download_desc": "다운로드 → [BGM 제거] → 분할 → 세그먼트 선택",
        "menu_transcribe": "📝 전사(Transcribe)부터 시작",
        "menu_transcribe_desc": "기존 오디오 → 전사 → [후처리] → TTS",
        "menu_generate": "🎤 음성 생성만",
        "menu_generate_desc": "기존 전사 → [후처리] → TTS",
        "menu_postprocess": "🔊 후처리만",
        "menu_postprocess_desc": "기존 오디오 향상 (노이즈 제거, EQ, 음량 정규화)",
        "menu_exit": "❌ 종료",
        "cancel": "❌ 취소",
        # Post-processing
        "postprocess_title": "후처리 설정",
        "postprocess_subtitle": "⚠️  주의: 목소리 품질이 저하될 수 있음. 건너뛰기 권장.",
        "postprocess_enable": "✅ 후처리 활성화",
        "postprocess_enable_desc": "노이즈 제거, EQ, 다이나믹스 적용 (목소리 특성 손실 가능)",
        "postprocess_disable": "⏭️  후처리 건너뛰기 (권장)",
        "postprocess_disable_desc": "원본 TTS 출력 유지 - 더 좋은 음질",
        "postprocess_complete": "✨ 후처리 완료!",
        "postprocess_files": "{n}개 파일 처리됨",
        # Source separation
        "separate_title": "음원 분리",
        "separate_subtitle": "⚠️  주의: 배경음악이 있으면 음성 복제 품질이 저하됩니다",
        "separate_enable": "🎵 배경음악 제거 (권장)",
        "separate_enable_desc": "Demucs AI로 보컬만 추출 - 더 깨끗한 레퍼런스 오디오",
        "separate_disable": "⏭️  배경음악 제거 건너뛰기",
        "separate_disable_desc": "원본 오디오 사용 (이미 깨끗한 경우만)",
        "separate_running": "배경음악에서 보컬 분리 중...",
        "separate_complete": "✅ 보컬 추출 완료",
        "separate_not_installed": "Demucs가 설치되지 않았습니다. 실행: pip install demucs",
        # Transcribe Language
        "transcribe_lang_title": "전사 언어",
        "transcribe_lang_subtitle": "전사할 오디오의 언어를 선택하세요",
        # TTS Language
        "tts_lang_title": "TTS 언어",
        "tts_lang_subtitle": "생성할 음성의 언어를 선택하세요",
        # Split mode
        "split_title": "분할 모드 선택",
        "split_subtitle": "오디오 분할 방식을 선택하세요",
        "split_auto": "🔄 자동 분할",
        "split_auto_desc": "30초 간격으로 자동 분할",
        "split_manual": "⏱️  수동 입력",
        "split_manual_desc": "시작 시간을 직접 입력",
        # Segment selection
        "segment_title": "세그먼트 선택",
        "segment_subtitle": "깨끗한 음성 구간을 선택하세요 (afplay로 미리듣기 가능)",
        # Steps
        "step_device": "Device Environment Check",
        "step_download": "Step 1: Download Audio from YouTube",
        "step_split": "Step 2: Split Audio into Segments",
        "step_select": "Step 3: Select Clean Voice Segment",
        "step_transcribe": "Step 4: Transcribe Audio",
        "step_setup": "Step 5: Setup Qwen3-TTS 1.7B Model",
        "step_generate": "Step 6: Generate Notification Voice Lines",
        # Messages
        "total_duration": "총 길이",
        "enter_start_time": "시작 시간을 입력하세요 (초 또는 MM:SS 형식, 빈 입력시 종료)",
        "start_time_prompt": "시작 시간: ",
        "invalid_format": "잘못된 형식입니다. 예: 30, 1:30, 90.5",
        "out_of_range": "범위를 벗어났습니다",
        "created_segments": "{n}개의 세그먼트 생성됨",
        "preview_tip": "💡 Tip: 터미널에서 'afplay <path>' (Mac) 또는 'aplay <path>' (Linux)로 미리듣기",
        "youtube_url_prompt": "Enter시 기본값 사용",
        "audio_ready": "✅ 오디오 준비 완료!",
        "pipeline_complete": "✨ 파이프라인 완료!",
        "next_steps": "다음 단계:",
        "next_step_1": "1. 생성된 오디오 파일 확인",
        "next_step_2": "2. ~/.claude/sounds/에 복사",
        "next_step_3": "3. Claude Code notification hook 설정",
        "bye": "👋 안녕히 가세요!",
        "cancelled": "취소됨",
        "no_gpu_warning": "GPU가 감지되지 않았습니다. CPU 사용 (느림)",
        "cleanup_title": "정리",
        "cleanup_confirm": "기존 출력 파일을 삭제할까요?",
        "cleanup_yes": "🗑️  예, 모두 삭제",
        "cleanup_yes_desc": "output/notifications의 모든 .wav 파일 삭제",
        "cleanup_no": "⏭️  아니오, 유지",
        "cleanup_no_desc": "기존 파일 유지 (일부 덮어쓸 수 있음)",
        "cleanup_deleted": "{n}개의 기존 파일 삭제됨",
        "cleanup_skipped": "기존 파일 유지",
    },
}


def t(key: str, **kwargs) -> str:
    """Get translated text for current language."""
    text = TEXTS.get(LANG, TEXTS["en"]).get(key, TEXTS["en"].get(key, key))
    if kwargs:
        return text.format(**kwargs)
    return text


def set_language(lang: str):
    """Set the current language."""
    global LANG
    LANG = lang


# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
RAW_AUDIO_DIR = OUTPUT_DIR / "raw"
CLEAN_AUDIO_DIR = OUTPUT_DIR / "clean"
TRANSCRIPTS_DIR = OUTPUT_DIR / "transcripts"
NOTIFICATIONS_DIR = OUTPUT_DIR / "notifications"
MODELS_DIR = PROJECT_ROOT / "models"

# Notification lines config file
NOTIFICATION_LINES_FILE = PROJECT_ROOT / "notification_lines.json"


def load_notification_lines() -> dict:
    """Load notification lines from JSON file."""
    with open(NOTIFICATION_LINES_FILE, encoding="utf-8") as f:
        return json.load(f)


# ============== Interactive Menu ==============


class InteractiveMenu:
    """Beautiful interactive menu with arrow key navigation."""

    def __init__(self, title: str, options: list[dict], subtitle: str = ""):
        self.title = title
        self.subtitle = subtitle
        self.options = options
        self.selected = 0

    def _render(self) -> Panel:
        """Render the menu."""
        menu_text = Text()

        for i, opt in enumerate(self.options):
            if i == self.selected:
                menu_text.append("  ▸ ", style="bold cyan")
                menu_text.append(f"{opt['label']}\n", style="bold white on blue")
                if opt.get("desc"):
                    menu_text.append(f"    {opt['desc']}\n", style="dim cyan")
            else:
                menu_text.append("    ", style="dim")
                menu_text.append(f"{opt['label']}\n", style="white")
                if opt.get("desc"):
                    menu_text.append(f"    {opt['desc']}\n", style="dim")

            if i < len(self.options) - 1:
                menu_text.append("\n")

        footer = Text(f"\n  {t('menu_footer')}", style="dim")
        menu_text.append(footer)

        return Panel(
            Align.left(menu_text),
            title=f"[bold magenta]✨ {self.title}[/bold magenta]",
            subtitle=f"[dim]{self.subtitle}[/dim]" if self.subtitle else None,
            border_style="magenta",
            box=box.ROUNDED,
            padding=(1, 2),
        )

    def run(self) -> int | None:
        """Run the interactive menu. Returns selected index or None if cancelled."""
        # Get key constants safely (different readchar versions)
        KEY_UP = getattr(readchar.key, "UP", "\x1b[A")
        KEY_DOWN = getattr(readchar.key, "DOWN", "\x1b[B")
        KEY_ENTER = getattr(readchar.key, "ENTER", "\r")
        KEY_ESCAPE = getattr(readchar.key, "ESCAPE", "\x1b")

        with Live(self._render(), console=console, refresh_per_second=30, transient=True) as live:
            while True:
                try:
                    key = readchar.readkey()

                    if key == KEY_UP:
                        self.selected = (self.selected - 1) % len(self.options)
                    elif key == KEY_DOWN:
                        self.selected = (self.selected + 1) % len(self.options)
                    elif key == KEY_ENTER or key == "\n":
                        return self.selected
                    elif key.lower() == "q" or key == KEY_ESCAPE:
                        return None
                    # Ignore all other keys

                    live.update(self._render())
                except Exception:
                    # Ignore any key reading errors
                    pass


def show_language_menu() -> str:
    """Show language selection menu."""
    options = [
        {"label": "🇺🇸 English", "desc": ""},
        {"label": "🇰🇷 한국어", "desc": ""},
    ]

    menu = InteractiveMenu(
        title="Language / 언어",
        subtitle="Select your language / 언어를 선택하세요",
        options=options,
    )

    result = menu.run()
    if result is None or result == 0:
        return "en"
    return "ko"


def show_main_menu() -> str | None:
    """Show main menu and return selected action."""
    options = [
        {"label": t("menu_full"), "desc": t("menu_full_desc"), "action": "full"},
        {"label": t("menu_download"), "desc": t("menu_download_desc"), "action": "download"},
        {"label": t("menu_transcribe"), "desc": t("menu_transcribe_desc"), "action": "transcribe"},
        {"label": t("menu_generate"), "desc": t("menu_generate_desc"), "action": "generate"},
        {
            "label": t("menu_postprocess"),
            "desc": t("menu_postprocess_desc"),
            "action": "postprocess",
        },
        {"label": t("menu_exit"), "desc": "", "action": "exit"},
    ]

    menu = InteractiveMenu(title=t("main_title"), subtitle=t("main_subtitle"), options=options)

    result = menu.run()
    if result is None:
        return None
    return options[result]["action"]


def show_segment_menu(segments: list[Path]) -> int | None:
    """Show segment selection menu."""
    options = [{"label": f"🎵 {seg.name}", "desc": ""} for seg in segments]
    options.append({"label": t("cancel"), "desc": ""})

    menu = InteractiveMenu(
        title=t("segment_title"), subtitle=t("segment_subtitle"), options=options
    )

    result = menu.run()
    if result is None or result == len(segments):
        return None
    return result


def show_split_mode_menu() -> str | None:
    """Show split mode selection menu."""
    options = [
        {"label": t("split_auto"), "desc": t("split_auto_desc")},
        {"label": t("split_manual"), "desc": t("split_manual_desc")},
        {"label": t("cancel"), "desc": ""},
    ]

    menu = InteractiveMenu(title=t("split_title"), subtitle=t("split_subtitle"), options=options)

    result = menu.run()
    if result is None or result == 2:
        return None
    return "auto" if result == 0 else "manual"


def show_postprocess_menu() -> bool:
    """Show post-processing enable/disable menu. Returns True if enabled."""
    options = [
        {"label": t("postprocess_disable"), "desc": t("postprocess_disable_desc")},
        {"label": t("postprocess_enable"), "desc": t("postprocess_enable_desc")},
    ]

    menu = InteractiveMenu(
        title=t("postprocess_title"), subtitle=t("postprocess_subtitle"), options=options
    )

    result = menu.run()
    # Default to disabled (skip) if cancelled - TTS output is already clean
    return result == 1


def show_cleanup_menu() -> bool:
    """Show cleanup confirmation menu. Returns True if user wants to delete existing files."""
    # Check if there are existing files
    if not NOTIFICATIONS_DIR.exists():
        return False

    existing_files = list(NOTIFICATIONS_DIR.rglob("*.wav"))
    if not existing_files:
        return False

    options = [
        {"label": t("cleanup_yes"), "desc": t("cleanup_yes_desc")},
        {"label": t("cleanup_no"), "desc": t("cleanup_no_desc")},
    ]

    menu = InteractiveMenu(
        title=t("cleanup_title"),
        subtitle=f"{t('cleanup_confirm')} ({len(existing_files)} files)",
        options=options,
    )

    result = menu.run()
    return result == 0


def cleanup_output_files():
    """Delete all existing wav files in the notifications output directory."""
    if not NOTIFICATIONS_DIR.exists():
        return 0

    deleted_count = 0
    for wav_file in NOTIFICATIONS_DIR.rglob("*.wav"):
        wav_file.unlink()
        deleted_count += 1

    # Also remove empty subdirectories
    for subdir in NOTIFICATIONS_DIR.iterdir():
        if subdir.is_dir() and not any(subdir.iterdir()):
            subdir.rmdir()

    return deleted_count


# Supported languages (Korean and English first, then alphabetical)
# tts_code: Qwen3-TTS language code, whisper_code: Whisper ISO 639-1 code
SUPPORTED_LANGUAGES = [
    {"tts_code": "korean", "whisper_code": "ko", "name": "Korean", "flag": "🇰🇷"},
    {"tts_code": "english", "whisper_code": "en", "name": "English", "flag": "🇺🇸"},
    {"tts_code": "chinese", "whisper_code": "zh", "name": "Chinese", "flag": "🇨🇳"},
    {"tts_code": "french", "whisper_code": "fr", "name": "French", "flag": "🇫🇷"},
    {"tts_code": "german", "whisper_code": "de", "name": "German", "flag": "🇩🇪"},
    {"tts_code": "italian", "whisper_code": "it", "name": "Italian", "flag": "🇮🇹"},
    {"tts_code": "japanese", "whisper_code": "ja", "name": "Japanese", "flag": "🇯🇵"},
    {"tts_code": "portuguese", "whisper_code": "pt", "name": "Portuguese", "flag": "🇵🇹"},
    {"tts_code": "russian", "whisper_code": "ru", "name": "Russian", "flag": "🇷🇺"},
    {"tts_code": "spanish", "whisper_code": "es", "name": "Spanish", "flag": "🇪🇸"},
]

# For backwards compatibility
TTS_LANGUAGES = SUPPORTED_LANGUAGES


def show_transcribe_language_menu() -> str:
    """Show transcribe language selection menu. Returns Whisper language code."""
    options = [
        {"label": f"{lang['flag']} {lang['name']}", "desc": ""} for lang in SUPPORTED_LANGUAGES
    ]

    menu = InteractiveMenu(
        title=t("transcribe_lang_title"), subtitle=t("transcribe_lang_subtitle"), options=options
    )

    result = menu.run()
    if result is None:
        return "ko"  # Default Korean
    return SUPPORTED_LANGUAGES[result]["whisper_code"]


def show_tts_language_menu() -> str:
    """Show TTS language selection menu. Returns TTS language code."""
    options = [
        {"label": f"{lang['flag']} {lang['name']}", "desc": ""} for lang in SUPPORTED_LANGUAGES
    ]

    menu = InteractiveMenu(
        title=t("tts_lang_title"), subtitle=t("tts_lang_subtitle"), options=options
    )

    result = menu.run()
    if result is None:
        return "korean"  # Default
    return SUPPORTED_LANGUAGES[result]["tts_code"]


# Human-friendly descriptions for the backend menu.
_BACKEND_DESC = {
    "qwen3": "Qwen3-TTS 1.7B",
}


def show_backend_menu() -> str:
    """Show TTS backend (model) selection menu. Returns the backend name."""
    names = available_backends()
    options = [{"label": f"🧩 {n}", "desc": _BACKEND_DESC.get(n, "")} for n in names]

    menu = InteractiveMenu(
        title="TTS Model / 음성 모델",
        subtitle=f"Select the voice-cloning engine (default: {DEFAULT_BACKEND})",
        options=options,
    )
    result = menu.run()
    if result is None:
        return DEFAULT_BACKEND
    return names[result]


def show_source_separation_menu() -> bool:
    """Show source separation (BGM removal) menu. Returns True if enabled."""
    options = [
        {"label": t("separate_enable"), "desc": t("separate_enable_desc")},
        {"label": t("separate_disable"), "desc": t("separate_disable_desc")},
    ]

    menu = InteractiveMenu(
        title=t("separate_title"), subtitle=t("separate_subtitle"), options=options
    )

    result = menu.run()
    # Default to enabled if cancelled - BGM degrades voice cloning quality
    if result is None:
        return True
    return result == 0


def parse_time_input(time_str: str) -> float | None:
    """Parse time input in various formats (seconds or MM:SS)."""
    time_str = time_str.strip()
    if not time_str:
        return None

    # MM:SS format
    if ":" in time_str:
        parts = time_str.split(":")
        if len(parts) == 2:
            try:
                minutes = int(parts[0])
                seconds = float(parts[1])
                return minutes * 60 + seconds
            except ValueError:
                return None
    # Seconds only
    else:
        try:
            return float(time_str)
        except ValueError:
            return None


# ============== Device Check ==============


def check_device() -> DeviceInfo:
    """Check and display device information."""
    console.print(Panel("[bold]Device Environment Check[/bold]", style="blue"))
    device_info = detect_device()
    print_device_info(device_info)
    if not device_info.is_gpu:
        logger.warning("No GPU detected, using CPU (will be slow)")
    console.print()
    return device_info


# ============== Pipeline Steps ==============


def download_audio(url: str, output_name: str = "karina_sample") -> Path:
    """Download audio from YouTube."""
    console.print(Panel("[bold]Step 1: Download Audio from YouTube[/bold]", style="blue"))

    RAW_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RAW_AUDIO_DIR / f"{output_name}.%(ext)s"

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format",
        "wav",
        "--audio-quality",
        "0",
        "-o",
        str(output_path),
        url,
    ]
    logger.info(f"Downloading: {url}")

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        progress.add_task("Downloading...", total=None)
        subprocess.run(cmd, check=True)

    downloaded = list(RAW_AUDIO_DIR.glob(f"{output_name}.*"))
    if downloaded:
        logger.success(f"Downloaded: {downloaded[0]}")
        console.print()
        return downloaded[0]
    raise FileNotFoundError("Download failed")


def separate_vocals_from_audio(input_file: Path, device_info: DeviceInfo) -> Path:
    """Separate vocals from background music using Demucs."""
    console.print(
        Panel("[bold]Step 1.5: Separate Vocals from Background Music (Demucs)[/bold]", style="blue")
    )

    try:
        from post_process import check_demucs_available, separate_vocals_to_file
    except ImportError:
        logger.error(t("separate_not_installed"))
        return input_file

    if not check_demucs_available():
        logger.error(t("separate_not_installed"))
        return input_file

    # Determine device for demucs
    if device_info.device_type == DeviceType.CUDA:
        device = "cuda"
    elif device_info.device_type == DeviceType.MPS:
        device = "mps"
    else:
        device = "cpu"

    output_path = RAW_AUDIO_DIR / f"{input_file.stem}_vocals.wav"

    logger.info(t("separate_running"))
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        progress.add_task(t("separate_running"), total=None)
        try:
            separate_vocals_to_file(
                input_file,
                output_path,
                model="htdemucs",
                device=device,
                quiet=True,
            )
            # Normalize audio to prevent clipping warnings in TTS
            normalize_audio_file(output_path)
            logger.success(t("separate_complete"))
            console.print()
            return output_path
        except Exception as e:
            logger.error(f"Source separation failed: {e}")
            logger.warning("Falling back to original audio")
            console.print()
            return input_file


def split_audio(input_file: Path, segment_duration: int = 15) -> list[Path]:
    """Split audio into segments."""
    console.print(Panel("[bold]Step 2: Split Audio into Segments[/bold]", style="blue"))

    from pydub import AudioSegment

    CLEAN_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading: {input_file}")
    audio = AudioSegment.from_file(input_file)
    total_duration = len(audio) / 1000
    logger.info(f"Total duration: {total_duration:.1f} seconds")

    # Show split mode selection menu
    mode = show_split_mode_menu()
    if mode is None:
        return []

    segments = []
    segment_ms = segment_duration * 1000

    if mode == "manual":
        # Manual mode: user inputs start times
        console.print(f"\n[cyan]{t('total_duration')}: {total_duration:.1f}s[/cyan]")
        console.print(f"[dim]{t('enter_start_time')}[/dim]\n")

        while True:
            try:
                time_input = console.input(f"[bold green]{t('start_time_prompt')}[/bold green]")
                if not time_input.strip():
                    break

                start_sec = parse_time_input(time_input)
                if start_sec is None:
                    console.print(f"[red]{t('invalid_format')}[/red]")
                    continue

                if start_sec < 0 or start_sec >= total_duration:
                    console.print(f"[red]{t('out_of_range')} (0 ~ {total_duration:.1f}s)[/red]")
                    continue

                start_ms = int(start_sec * 1000)
                end_ms = min(start_ms + segment_ms, len(audio))

                segment = audio[start_ms:end_ms]
                segment = segment.normalize()
                segment = segment.set_frame_rate(16000).set_channels(1)

                end_sec = end_ms / 1000
                filename = f"segment_{int(start_sec)}s_{int(end_sec)}s.wav"
                output_path = CLEAN_AUDIO_DIR / filename

                segment.export(output_path, format="wav")
                segments.append(output_path)
                logger.success(f"Created: {filename} ({end_sec - start_sec:.1f}초)")

            except KeyboardInterrupt:
                break
    else:
        # Auto mode: split at 30-second intervals
        segment_starts = list(range(0, len(audio), 30000))

        with Progress(
            SpinnerColumn(style="magenta"),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(complete_style="magenta", finished_style="green"),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Splitting audio...", total=len(segment_starts))

            for start_ms in segment_starts:
                end_ms = min(start_ms + segment_ms, len(audio))
                if end_ms - start_ms < 5000:
                    progress.advance(task)
                    continue

                segment = audio[start_ms:end_ms]
                segment = segment.normalize()
                segment = segment.set_frame_rate(16000).set_channels(1)

                start_sec = start_ms // 1000
                end_sec = end_ms // 1000
                filename = f"segment_{start_sec}s_{end_sec}s.wav"
                output_path = CLEAN_AUDIO_DIR / filename

                segment.export(output_path, format="wav")
                segments.append(output_path)
                progress.advance(task)

    logger.success(f"Created {len(segments)} segments")
    console.print()
    return segments


def select_segment(segments: list[Path]) -> Path | None:
    """Let user select a segment using interactive menu."""
    console.print(Panel("[bold]Step 3: Select Clean Voice Segment[/bold]", style="blue"))

    # Show segments info
    table = Table(title="Available Segments", box=box.ROUNDED)
    table.add_column("Index", style="cyan")
    table.add_column("Filename", style="green")
    for i, seg in enumerate(segments):
        table.add_row(str(i), seg.name)
    console.print(table)
    console.print(f"\n[dim]{t('preview_tip')}[/dim]\n")

    # Interactive selection
    idx = show_segment_menu(segments)
    if idx is None:
        return None

    selected = segments[idx]
    logger.success(f"Selected: {selected.name}")

    final_path = CLEAN_AUDIO_DIR / "karina_clean.wav"
    shutil.copy(selected, final_path)
    logger.info(f"Copied to: {final_path}")

    # Normalize audio to prevent clipping warnings in TTS
    normalize_audio_file(final_path)

    console.print()
    return final_path


def transcribe_audio(audio_path: Path, device_info: DeviceInfo, language: str = "ko") -> str:
    """Transcribe audio using the appropriate backend for the platform."""
    console.print(
        Panel(
            f"[bold]Step 4: Transcribe Audio ({device_info.whisper_backend})[/bold]", style="blue"
        )
    )

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Transcribe language: {language}")

    if device_info.whisper_backend == "faster-whisper":
        from faster_whisper import WhisperModel

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            progress.add_task("Loading Whisper large-v3 model...", total=None)
            compute_type = "float16" if device_info.dtype == torch.float16 else "float32"
            model = WhisperModel(
                "large-v3", device=device_info.device_type.value, compute_type=compute_type
            )

        logger.info(f"Transcribing: {audio_path}")
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            progress.add_task("Transcribing...", total=None)
            segments, info = model.transcribe(str(audio_path), language=language)
            text = " ".join([seg.text for seg in segments])
    else:
        import mlx_whisper

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            progress.add_task("Loading Whisper large-v3 model (MLX)...", total=None)
        logger.info(f"Transcribing: {audio_path}")
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            progress.add_task("Transcribing...", total=None)
            result = mlx_whisper.transcribe(
                str(audio_path),
                path_or_hf_repo="mlx-community/whisper-large-v3-mlx",
                language=language,
            )
            text = result["text"]
            info = type("Info", (), {"language": language})()

    result_data = {"text": text, "language": info.language}
    output_path = TRANSCRIPTS_DIR / f"{audio_path.stem}_transcript.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.success(f"Transcript saved to: {output_path}")
    console.print(Panel(f"[italic]{text}[/italic]", title="Transcript", style="green"))
    console.print()
    return text


def setup_tts_model():
    """Download and setup Qwen3-TTS 1.7B model."""
    console.print(Panel("[bold]Step 5: Setup Qwen3-TTS 1.7B Model[/bold]", style="blue"))

    from huggingface_hub import snapshot_download

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    model_name = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    local_dir = MODELS_DIR / "Qwen3-TTS-12Hz-1.7B-Base"

    if not local_dir.exists():
        logger.info(f"Downloading {model_name}...")
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            progress.add_task("Downloading model...", total=None)
            snapshot_download(repo_id=model_name, local_dir=str(local_dir))
    else:
        logger.info(f"Model already exists: {local_dir}")

    tokenizer_name = "Qwen/Qwen3-TTS-Tokenizer-12Hz"
    tokenizer_dir = MODELS_DIR / "Qwen3-TTS-Tokenizer-12Hz"

    if not tokenizer_dir.exists():
        logger.info(f"Downloading {tokenizer_name}...")
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            progress.add_task("Downloading tokenizer...", total=None)
            snapshot_download(repo_id=tokenizer_name, local_dir=str(tokenizer_dir))
    else:
        logger.info(f"Tokenizer already exists: {tokenizer_dir}")

    logger.success("Model setup complete!")
    console.print()
    return local_dir


def normalize_audio(audio: np.ndarray) -> np.ndarray:
    """Normalize audio to [-1.0, 1.0] range to prevent clipping warnings."""
    max_val = np.max(np.abs(audio))
    if max_val > 1.0:
        return audio / max_val
    return audio


def normalize_audio_file(audio_path: Path) -> None:
    """Normalize an audio file in place to [-1.0, 1.0] range."""
    audio, sr = sf.read(str(audio_path))
    max_val = np.max(np.abs(audio))
    if max_val > 1.0:
        logger.info(f"Normalizing audio (peak: {max_val:.2f} -> 1.0)")
        audio = audio / max_val
        sf.write(str(audio_path), audio, sr)


def add_silence(audio: np.ndarray, sr: int, silence_ms: int = 300) -> tuple[np.ndarray, int]:
    """Add silence at the beginning of audio."""
    silence_samples = int(sr * silence_ms / 1000)
    silence = np.zeros(silence_samples, dtype=audio.dtype)
    return np.concatenate([silence, audio]), sr


def enhance_audio(audio: np.ndarray, sr: int) -> np.ndarray:
    """Apply audio enhancement: denoise, EQ, dynamics, loudness normalization."""
    try:
        from post_process import post_process_audio as pp_audio

        return pp_audio(
            audio,
            sr,
            denoise=True,
            eq=True,
            dynamics=True,
            loudness_normalize=True,
            target_lufs=-14.0,
            denoise_strength=0.6,
        )
    except ImportError as e:
        logger.warning(f"Post-processing dependencies not installed: {e}. Skipping enhancement.")
        return audio
    except Exception as e:
        logger.warning(f"Post-processing failed: {e}. Skipping enhancement.")
        return audio


def generate_notifications(
    ref_audio_path: Path,
    ref_text: str,
    backend,
    device_info: DeviceInfo,
    enable_postprocess: bool = True,
    tts_language: str = "korean",
):
    """Generate all notification voice lines with the chosen (already-loaded) backend."""
    console.print(
        Panel(
            f"[bold]Step 6: Generate Notification Voice Lines "
            f"({backend.name} / {device_info.device_type.value.upper()})[/bold]",
            style="blue",
        )
    )

    NOTIFICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    notification_lines = load_notification_lines()

    # Backends speak ISO codes; the TTS menu returns full names ("korean").
    iso_lang = next(
        (lang["whisper_code"] for lang in SUPPORTED_LANGUAGES if lang["tts_code"] == tts_language),
        "ko",
    )

    total = sum(len(lines) for lines in notification_lines.values())
    logger.info(f"Generating {total} notification voice lines with '{backend.name}'...")
    logger.info(f"Reference audio: {ref_audio_path}")
    logger.info(f"TTS language: {tts_language} ({iso_lang})")
    logger.info(f"Post-processing: {'enabled' if enable_postprocess else 'disabled'}")

    with Progress(
        SpinnerColumn(style="magenta"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(complete_style="magenta", finished_style="green"),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Generating notifications...", total=total)

        for notification_type, lines in notification_lines.items():
            type_dir = NOTIFICATIONS_DIR / notification_type
            type_dir.mkdir(parents=True, exist_ok=True)

            for line in lines:
                output_path = type_dir / line["filename"]
                audio, sr = backend.clone(line["text"], ref_audio_path, ref_text, iso_lang)

                # Add silence at the beginning
                audio_with_silence, sr = add_silence(audio, sr, silence_ms=300)
                # Apply audio enhancement if enabled
                if enable_postprocess:
                    final_audio = enhance_audio(audio_with_silence, sr)
                else:
                    final_audio = audio_with_silence
                sf.write(str(output_path), final_audio, sr)
                progress.advance(task)

    logger.success(f"All notifications generated in: {NOTIFICATIONS_DIR}")

    table = Table(title="Generated Files", box=box.ROUNDED)
    table.add_column("Type", style="cyan")
    table.add_column("File", style="green")
    for notification_type in notification_lines:
        type_dir = NOTIFICATIONS_DIR / notification_type
        for f in type_dir.glob("*.wav"):
            table.add_row(notification_type, f.name)
    console.print(table)
    console.print()


def show_completion():
    """Show completion message."""
    console.print(
        Panel.fit(
            f"[bold green]{t('pipeline_complete')}[/bold green]\n\n"
            f"Generated notifications are in: [cyan]{NOTIFICATIONS_DIR}[/cyan]\n\n"
            f"[dim]{t('next_steps')}[/dim]\n"
            f"{t('next_step_1')}\n"
            f"{t('next_step_2')}\n"
            f"{t('next_step_3')}",
            border_style="green",
        )
    )


# ============== Main Entry Points ==============

DEFAULT_YOUTUBE_URL = "https://www.youtube.com/watch?v=r96zEiIHVf4"


def run_full_pipeline(url: str, device_info: DeviceInfo):
    """Run the complete pipeline."""
    # Ask about cleanup if there are existing output files
    if show_cleanup_menu():
        deleted = cleanup_output_files()
        logger.info(t("cleanup_deleted", n=deleted))
    else:
        # Check if there were files but user chose to keep them
        if NOTIFICATIONS_DIR.exists() and list(NOTIFICATIONS_DIR.rglob("*.wav")):
            logger.info(t("cleanup_skipped"))

    audio_file = download_audio(url)
    # Ask about source separation (BGM removal)
    enable_separation = show_source_separation_menu()
    if enable_separation:
        audio_file = separate_vocals_from_audio(audio_file, device_info)
    segments = split_audio(audio_file)
    selected_segment = select_segment(segments)
    if selected_segment is None:
        logger.info("Cancelled")
        return
    # Ask about transcribe language
    transcribe_lang = show_transcribe_language_menu()
    transcript = transcribe_audio(selected_segment, device_info, transcribe_lang)
    # Ask which TTS model (backend)
    backend_name = show_backend_menu()
    # Ask about TTS language
    tts_language = show_tts_language_menu()
    # Ask about post-processing
    enable_postprocess = show_postprocess_menu()
    backend = get_backend(backend_name)
    backend.load(device_info)
    generate_notifications(
        selected_segment, transcript, backend, device_info, enable_postprocess, tts_language
    )
    show_completion()


def run_download_only(url: str, device_info: DeviceInfo):
    """Download and split audio only."""
    audio_file = download_audio(url)
    # Ask about source separation (BGM removal)
    enable_separation = show_source_separation_menu()
    if enable_separation:
        audio_file = separate_vocals_from_audio(audio_file, device_info)
    segments = split_audio(audio_file)
    selected = select_segment(segments)
    if selected:
        console.print(
            Panel.fit(
                f"[bold green]{t('audio_ready')}[/bold green]\n\nSelected: [cyan]{selected}[/cyan]",
                border_style="green",
            )
        )


def run_from_transcribe(device_info: DeviceInfo):
    """Run from transcribe step using existing audio."""
    # Ask about cleanup if there are existing output files
    if show_cleanup_menu():
        deleted = cleanup_output_files()
        logger.info(t("cleanup_deleted", n=deleted))
    elif NOTIFICATIONS_DIR.exists() and list(NOTIFICATIONS_DIR.rglob("*.wav")):
        logger.info(t("cleanup_skipped"))

    clean_audio = CLEAN_AUDIO_DIR / "karina_clean.wav"
    if not clean_audio.exists():
        existing = list(CLEAN_AUDIO_DIR.glob("segment_*.wav"))
        if not existing:
            logger.error(f"No audio files found in {CLEAN_AUDIO_DIR}")
            logger.error("Please run 'Download & Extract' first")
            return
        selected = select_segment(existing)
        if selected is None:
            return
        clean_audio = selected

    # Ask about transcribe language
    transcribe_lang = show_transcribe_language_menu()
    transcript = transcribe_audio(clean_audio, device_info, transcribe_lang)
    # Ask which TTS model (backend)
    backend_name = show_backend_menu()
    # Ask about TTS language
    tts_language = show_tts_language_menu()
    # Ask about post-processing
    enable_postprocess = show_postprocess_menu()
    backend = get_backend(backend_name)
    backend.load(device_info)
    generate_notifications(
        clean_audio, transcript, backend, device_info, enable_postprocess, tts_language
    )
    show_completion()


def run_generate_only(device_info: DeviceInfo):
    """Generate notifications using existing transcript."""
    # Ask about cleanup if there are existing output files
    if show_cleanup_menu():
        deleted = cleanup_output_files()
        logger.info(t("cleanup_deleted", n=deleted))
    elif NOTIFICATIONS_DIR.exists() and list(NOTIFICATIONS_DIR.rglob("*.wav")):
        logger.info(t("cleanup_skipped"))

    clean_audio = CLEAN_AUDIO_DIR / "karina_clean.wav"
    if not clean_audio.exists():
        logger.error(f"No clean audio found: {clean_audio}")
        logger.error("Please run 'Transcribe' first")
        return

    transcript_files = list(TRANSCRIPTS_DIR.glob("*_transcript.json"))
    if not transcript_files:
        logger.error(f"No transcript found in {TRANSCRIPTS_DIR}")
        logger.error("Please run 'Transcribe' first")
        return

    with open(transcript_files[0], encoding="utf-8") as f:
        transcript_data = json.load(f)
    transcript = transcript_data["text"]

    logger.info(f"Using transcript: {transcript[:50]}...")
    # Ask which TTS model (backend)
    backend_name = show_backend_menu()
    # Ask about TTS language
    tts_language = show_tts_language_menu()
    # Ask about post-processing
    enable_postprocess = show_postprocess_menu()
    backend = get_backend(backend_name)
    backend.load(device_info)
    generate_notifications(
        clean_audio, transcript, backend, device_info, enable_postprocess, tts_language
    )
    show_completion()


def run_postprocess_only():
    """Run post-processing on existing notification files."""
    console.print(Panel(f"[bold]{t('postprocess_title')}[/bold]", style="blue"))

    # Check if notifications directory exists
    if not NOTIFICATIONS_DIR.exists():
        logger.error(f"No notifications found in {NOTIFICATIONS_DIR}")
        logger.error("Please run 'Generate' first")
        return

    wav_files = list(NOTIFICATIONS_DIR.rglob("*.wav"))
    if not wav_files:
        logger.error(f"No .wav files found in {NOTIFICATIONS_DIR}")
        return

    logger.info(f"Found {len(wav_files)} audio files to process")

    try:
        from post_process import post_process_directory

        processed = post_process_directory(
            NOTIFICATIONS_DIR,
            denoise=True,
            eq=True,
            dynamics=True,
            loudness_normalize=True,
            target_lufs=-14.0,
            denoise_strength=0.6,
        )
        console.print(
            Panel.fit(
                f"[bold green]{t('postprocess_complete')}[/bold green]\n\n"
                f"{t('postprocess_files', n=len(processed))}\n"
                f"Location: [cyan]{NOTIFICATIONS_DIR}[/cyan]",
                border_style="green",
            )
        )
    except ImportError:
        logger.error("Post-processing dependencies not installed.")
        logger.error("Run: pixi run install-deps-mac (or install-deps-linux)")


def main():
    parser = argparse.ArgumentParser(description="Karina Voice Notification Generator")
    parser.add_argument("url", nargs="?", default=DEFAULT_YOUTUBE_URL, help="YouTube URL")
    parser.add_argument(
        "--skip-download", action="store_true", help="Skip download, use existing audio"
    )
    parser.add_argument("--no-menu", action="store_true", help="Skip menu, run full pipeline")
    parser.add_argument("--lang", choices=["en", "ko"], default=None, help="Language (en/ko)")
    args = parser.parse_args()

    # Show banner
    console.print(
        Panel.fit(
            "[bold magenta]🎤 Karina Voice Notification Generator[/bold magenta]\n"
            "[dim]Cross-platform (CUDA / MPS / CPU)[/dim]",
            border_style="magenta",
        )
    )
    console.print()

    # Language selection
    if args.lang:
        set_language(args.lang)
    else:
        lang = show_language_menu()
        set_language(lang)
    console.print()

    # Check device
    device_info = check_device()

    # If --no-menu or --skip-download, run full pipeline directly
    if args.no_menu or args.skip_download:
        if args.skip_download:
            run_from_transcribe(device_info)
        else:
            run_full_pipeline(args.url, device_info)
        return

    # Show interactive menu
    while True:
        action = show_main_menu()

        if action is None or action == "exit":
            console.print(f"\n[dim]{t('bye')}[/dim]")
            break
        elif action == "full":
            url = (
                console.input(
                    f"\n[bold yellow]YouTube URL[/bold yellow] [dim]({t('youtube_url_prompt')})[/dim]: "
                ).strip()
                or args.url
            )
            run_full_pipeline(url, device_info)
        elif action == "download":
            url = (
                console.input(
                    f"\n[bold yellow]YouTube URL[/bold yellow] [dim]({t('youtube_url_prompt')})[/dim]: "
                ).strip()
                or args.url
            )
            run_download_only(url, device_info)
        elif action == "transcribe":
            run_from_transcribe(device_info)
        elif action == "generate":
            run_generate_only(device_info)
        elif action == "postprocess":
            run_postprocess_only()

        console.print("\n")


if __name__ == "__main__":
    main()
