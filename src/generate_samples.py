#!/usr/bin/env python3
"""
Batch generator for README voice samples.

Non-interactive counterpart to pipeline.py: clones several celebrities' voices
from interview URLs and makes each speak a set of sample lines in both Korean
and English, so the README can showcase the tool with real, comparable audio.

Matrix: CELEBRITIES x CASES x LANGUAGES  (3 x 3 x 2 = 18 clips)

For every celebrity it runs ONCE:
    YouTube -> download -> BGM removal (Demucs) -> auto segment pick
            -> transcribe (Whisper)                     [builds the reference]
then reuses that reference to clone every case/language via Qwen3-TTS.
(The Korean reference timbre carries over to English via cross-lingual cloning.)

Outputs land in output/samples/<celeb>_<case>_<lang>.wav and are copied to
assets/samples/ so they can be committed and linked from the README.

Usage:
    python src/generate_samples.py            # all celebrities
    python src/generate_samples.py karina     # a subset by id
"""

import shutil
import subprocess
import sys
import warnings
from pathlib import Path

# Ensure src/ is importable when run from the project root
_src_dir = Path(__file__).parent.resolve()
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

import soundfile as sf
import torch
import transformers
from loguru import logger
from pydub import AudioSegment

from device_utils import DeviceType, detect_device, print_device_info
from pipeline import add_silence, normalize_audio_file, setup_tts_model, transcribe_audio

transformers.logging.set_verbosity_error()
warnings.filterwarnings("ignore", message=".*pad_token_id.*")

logger.remove()
logger.add(sys.stderr, format="<level>{level: <7}</level> | {message}")

# ============== Config ==============

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
SAMPLES_RAW_DIR = OUTPUT_DIR / "samples" / "raw"
SAMPLES_CLEAN_DIR = OUTPUT_DIR / "samples" / "clean"
SAMPLES_OUT_DIR = OUTPUT_DIR / "samples"
ASSETS_SAMPLES_DIR = PROJECT_ROOT / "assets" / "samples"

# Reference-audio window length (seconds) for voice cloning.
SEGMENT_SECONDS = 12

# The three showcase voices. Order = README display order.
CELEBRITIES = [
    {"id": "karina", "name": "카리나 (aespa)", "url": "https://www.youtube.com/watch?v=r96zEiIHVf4"},
    {"id": "chaeyoung", "name": "이채영", "url": "https://www.youtube.com/watch?v=8amfOc9d02I"},
    {"id": "eunbin", "name": "박은빈", "url": "https://www.youtube.com/watch?v=1UcsKU7gY2k"},
]

# Three sample lines, each generated in every voice and every language.
# Korean text mirrors notification_lines.json; English is a natural equivalent.
CASES = [
    {
        "id": "done",
        "ko": "다 끝났어요! 결과 한번 확인해주세요~",
        "en": "All done! Please take a look at the results.",
    },
    {
        "id": "permission",
        "ko": "잠깐만요! 이거 실행해도 괜찮을까요? 허락해주세요~",
        "en": "Wait a second! Is it okay to run this? Please allow it.",
    },
    {
        "id": "auth",
        "ko": "인증이 완료되었어요! 도와주셔서 정말 고마워요~",
        "en": "Authentication complete! Thank you so much for your help.",
    },
]

# Qwen3-TTS language code + text key in CASES + filename suffix.
LANGUAGES = [
    {"code": "korean", "key": "ko", "suffix": "ko"},
    {"code": "english", "key": "en", "suffix": "en"},
]


# ============== Pipeline steps ==============


def download_audio(url: str, output_name: str) -> Path:
    """Download best-quality audio from YouTube as wav (skips if already present)."""
    SAMPLES_RAW_DIR.mkdir(parents=True, exist_ok=True)

    existing = list(SAMPLES_RAW_DIR.glob(f"{output_name}.*"))
    if existing:
        logger.info(f"[{output_name}] audio already downloaded: {existing[0].name}")
        return existing[0]

    output_path = SAMPLES_RAW_DIR / f"{output_name}.%(ext)s"
    cmd = [
        "yt-dlp", "-x", "--audio-format", "wav", "--audio-quality", "0",
        "-o", str(output_path), url,
    ]
    logger.info(f"[{output_name}] downloading: {url}")
    subprocess.run(cmd, check=True)

    downloaded = list(SAMPLES_RAW_DIR.glob(f"{output_name}.*"))
    if not downloaded:
        raise FileNotFoundError(f"Download failed for {url}")
    logger.success(f"[{output_name}] downloaded: {downloaded[0].name}")
    return downloaded[0]


def separate_vocals(input_file: Path, output_name: str, device_info) -> Path:
    """Strip background music with Demucs; fall back to the original on failure."""
    from post_process import check_demucs_available, separate_vocals_to_file

    if not check_demucs_available():
        logger.warning("Demucs not installed; using original audio")
        return input_file

    device = {
        DeviceType.CUDA: "cuda",
        DeviceType.MPS: "mps",
    }.get(device_info.device_type, "cpu")

    output_path = SAMPLES_RAW_DIR / f"{output_name}_vocals.wav"
    if output_path.exists():
        logger.info(f"[{output_name}] vocals already extracted")
        return output_path

    logger.info(f"[{output_name}] separating vocals (Demucs)...")
    try:
        separate_vocals_to_file(input_file, output_path, model="htdemucs", device=device, quiet=True)
        normalize_audio_file(output_path)
        logger.success(f"[{output_name}] vocals extracted")
        return output_path
    except Exception as e:
        logger.error(f"[{output_name}] source separation failed: {e}; using original")
        return input_file


def auto_select_segment(input_file: Path, output_name: str) -> Path:
    """
    Pick the loudest continuous SEGMENT_SECONDS window as the reference clip.

    After BGM removal only vocals remain, so peak-energy window ~= the most
    sustained speech. Replaces the manual "listen and choose" step of pipeline.py.
    """
    SAMPLES_CLEAN_DIR.mkdir(parents=True, exist_ok=True)

    audio = AudioSegment.from_file(input_file)
    window_ms = SEGMENT_SECONDS * 1000

    if len(audio) <= window_ms:
        best = audio
    else:
        hop_ms = 1000
        best, best_loudness = None, float("-inf")
        for start in range(0, len(audio) - window_ms + 1, hop_ms):
            window = audio[start : start + window_ms]
            if window.dBFS > best_loudness:
                best, best_loudness = window, window.dBFS
        logger.info(f"[{output_name}] picked window @ loudness {best_loudness:.1f} dBFS")

    best = best.normalize().set_frame_rate(16000).set_channels(1)
    output_path = SAMPLES_CLEAN_DIR / f"{output_name}_clean.wav"
    best.export(output_path, format="wav")
    normalize_audio_file(output_path)
    logger.success(f"[{output_name}] reference clip ready: {output_path.name}")
    return output_path


def prepare_reference(celeb: dict, device_info) -> dict | None:
    """Run download -> separate -> segment -> transcribe for one celebrity."""
    cid = celeb["id"]
    logger.info(f"=== Preparing reference for {celeb['name']} ({cid}) ===")
    try:
        raw = download_audio(celeb["url"], cid)
        vocals = separate_vocals(raw, cid, device_info)
        clip = auto_select_segment(vocals, cid)
        ref_text = transcribe_audio(clip, device_info, language="ko")
        return {**celeb, "ref_audio": clip, "ref_text": ref_text}
    except Exception as e:
        logger.error(f"[{cid}] reference preparation failed: {e}")
        return None


def generate_samples(refs: list[dict], model_path: Path, device_info) -> list[Path]:
    """Load Qwen3-TTS once and clone every case/language in each prepared voice."""
    from qwen_tts import Qwen3TTSModel

    SAMPLES_OUT_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading Qwen3-TTS on {device_info.device_type.value.upper()}...")
    model = Qwen3TTSModel.from_pretrained(
        str(model_path),
        dtype=device_info.dtype,
        attn_implementation=device_info.attn_implementation,
        device_map=device_info.torch_device,
    )
    if device_info.device_type == DeviceType.MPS:
        torch.mps.synchronize()

    total = len(refs) * len(CASES) * len(LANGUAGES)
    logger.info(f"Generating {total} clips ({len(refs)} voices x {len(CASES)} cases x {len(LANGUAGES)} langs)...")

    outputs = []
    for ref in refs:
        cid = ref["id"]
        for case in CASES:
            for lang in LANGUAGES:
                text = case[lang["key"]]
                stem = f"{cid}_{case['id']}_{lang['suffix']}"
                logger.info(f"[{stem}] cloning ({lang['code']}): {text}")
                wavs, sr = model.generate_voice_clone(
                    text=text,
                    ref_audio=str(ref["ref_audio"]),
                    ref_text=ref["ref_text"],
                    language=lang["code"],
                    non_streaming_mode=True,
                )
                if device_info.device_type == DeviceType.MPS:
                    torch.mps.synchronize()

                audio, sr = add_silence(wavs[0], sr, silence_ms=300)
                out_path = SAMPLES_OUT_DIR / f"{stem}.wav"
                sf.write(str(out_path), audio, sr)

                asset_path = ASSETS_SAMPLES_DIR / f"{stem}.wav"
                shutil.copy(out_path, asset_path)
                logger.success(f"[{stem}] saved -> {asset_path.relative_to(PROJECT_ROOT)}")
                outputs.append(asset_path)

    return outputs


def main():
    wanted = set(sys.argv[1:])
    celebs = [c for c in CELEBRITIES if not wanted or c["id"] in wanted]
    if not celebs:
        logger.error(f"No matching celebrities for {wanted}. Known: {[c['id'] for c in CELEBRITIES]}")
        sys.exit(1)

    logger.info(f"Cases: {[c['id'] for c in CASES]} | Languages: {[x['suffix'] for x in LANGUAGES]}")

    device_info = detect_device()
    print_device_info(device_info)

    refs = [r for c in celebs if (r := prepare_reference(c, device_info))]
    if not refs:
        logger.error("No references prepared; aborting.")
        sys.exit(1)

    model_path = setup_tts_model()
    outputs = generate_samples(refs, model_path, device_info)

    logger.success(f"Done. {len(outputs)} samples in {ASSETS_SAMPLES_DIR.relative_to(PROJECT_ROOT)}/")
    for p in outputs:
        logger.info(f"  - {p.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
