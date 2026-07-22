#!/usr/bin/env python3
"""One-shot voice-notification generator (non-interactive engine).

Given a YouTube URL and a set of notification lines, this clones the voice and
produces the full notification set in `output/notifications/`, ready to install
with `scripts/install_notifications.py`.

It is the engine behind the conversational **generate-voice** skill: the agent
collects the URL and the messages from the user, then calls this with `--line`
arguments (or a custom `--lines` JSON file).

Examples:
    # Use the default lines from notification_lines.json
    python src/quickstart.py "https://youtu.be/VIDEO_ID"

    # Override specific notifications inline (type:text), repeatable
    python src/quickstart.py "https://youtu.be/VIDEO_ID" \
        --line "idle_prompt:다 됐어요! 확인해 주세요~" \
        --line "permission_prompt:이거 실행해도 될까요?"

    # Or point at your own lines file
    python src/quickstart.py "https://youtu.be/VIDEO_ID" --lines my_lines.json
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

_src_dir = Path(__file__).parent.resolve()
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

import soundfile as sf
import torch
import transformers
from loguru import logger

from device_utils import DeviceType, detect_device, print_device_info
from generate_samples import auto_select_segment, download_audio, separate_vocals
from pipeline import add_silence, setup_tts_model, transcribe_audio

transformers.logging.set_verbosity_error()
warnings.filterwarnings("ignore", message=".*pad_token_id.*")
logger.remove()
logger.add(sys.stderr, format="<level>{level: <7}</level> | {message}")

PROJECT_ROOT = Path(__file__).parent.parent
NOTIFICATIONS_DIR = PROJECT_ROOT / "output" / "notifications"
DEFAULT_LINES_FILE = PROJECT_ROOT / "notification_lines.json"


def build_lines(lines_file: Path | None, overrides: list[str]) -> dict:
    """Load the base notification lines, then apply `type:text` overrides.

    An override replaces the FIRST line of that type; unknown types are added
    as a new single-line category named `<type>_1.wav`.
    """
    base = json.loads((lines_file or DEFAULT_LINES_FILE).read_text(encoding="utf-8"))
    for ov in overrides:
        if ":" not in ov:
            raise ValueError(f"--line must be 'type:text', got: {ov!r}")
        ntype, text = ov.split(":", 1)
        ntype, text = ntype.strip(), text.strip()
        if base.get(ntype):
            base[ntype][0]["text"] = text
        else:
            base[ntype] = [{"text": text, "filename": f"{ntype}_1.wav"}]
    return base


def prepare_reference(url: str, device_info, use_bgm_removal: bool) -> tuple[Path, str]:
    """Download → (BGM removal) → auto-select segment → transcribe."""
    logger.info(f"Preparing reference from: {url}")
    raw = download_audio(url, "quickstart")
    ref = separate_vocals(raw, "quickstart", device_info) if use_bgm_removal else raw
    clip = auto_select_segment(ref, "quickstart")
    text = transcribe_audio(clip, device_info, language="ko")
    return clip, text


def generate(lines: dict, ref_audio: Path, ref_text: str, model_path: Path, device_info,
             tts_language: str) -> list[Path]:
    """Clone every line into output/notifications/<type>/<filename>."""
    from qwen_tts import Qwen3TTSModel

    logger.info(f"Loading Qwen3-TTS on {device_info.device_type.value.upper()}...")
    model = Qwen3TTSModel.from_pretrained(
        str(model_path),
        dtype=device_info.dtype,
        attn_implementation=device_info.attn_implementation,
        device_map=device_info.torch_device,
    )
    if device_info.device_type == DeviceType.MPS:
        torch.mps.synchronize()

    total = sum(len(v) for v in lines.values())
    logger.info(f"Generating {total} notification clips...")

    outputs = []
    for ntype, items in lines.items():
        type_dir = NOTIFICATIONS_DIR / ntype
        type_dir.mkdir(parents=True, exist_ok=True)
        for item in items:
            logger.info(f"[{ntype}] {item['text']}")
            wavs, sr = model.generate_voice_clone(
                text=item["text"],
                ref_audio=str(ref_audio),
                ref_text=ref_text,
                language=tts_language,
                non_streaming_mode=True,
            )
            if device_info.device_type == DeviceType.MPS:
                torch.mps.synchronize()
            audio, sr = add_silence(wavs[0], sr, silence_ms=300)
            out = type_dir / item["filename"]
            sf.write(str(out), audio, sr)
            outputs.append(out)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="One-shot voice notification generator")
    parser.add_argument("url", help="YouTube URL of the reference voice")
    parser.add_argument("--line", action="append", default=[], metavar="TYPE:TEXT",
                        help="Override a notification line (repeatable)")
    parser.add_argument("--lines", type=Path, default=None,
                        help="Custom notification_lines.json (default: repo's file)")
    parser.add_argument("--language", default="korean", help="TTS language (default: korean)")
    parser.add_argument("--no-bgm-removal", action="store_true",
                        help="Skip Demucs BGM removal (use for already-clean audio)")
    args = parser.parse_args()

    lines = build_lines(args.lines, args.line)
    logger.info(f"Notification types: {list(lines)}")

    device_info = detect_device()
    print_device_info(device_info)

    ref_audio, ref_text = prepare_reference(args.url, device_info, not args.no_bgm_removal)
    model_path = setup_tts_model()
    outputs = generate(lines, ref_audio, ref_text, model_path, device_info, args.language)

    logger.success(f"Done — {len(outputs)} clips in {NOTIFICATIONS_DIR.relative_to(PROJECT_ROOT)}/")
    logger.info("Next: install into Claude Code / Codex with scripts/install_notifications.py")


if __name__ == "__main__":
    main()
