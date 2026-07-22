#!/usr/bin/env python3
"""
Batch generator for README voice samples.

Non-interactive counterpart to pipeline.py: clones several celebrities' voices
from interview URLs and makes each speak a set of sample lines in both Korean
and English, so the README can showcase the tool with real, comparable audio.

Matrix: CELEBRITIES x CASES x LANGUAGES  (defaults to Karina x 3 lines x Korean).
Add entries to CELEBRITIES / LANGUAGES to expand the matrix automatically.

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
import transformers
from loguru import logger
from pydub import AudioSegment

from device_utils import DeviceType, detect_device, print_device_info
from pipeline import add_silence, normalize_audio_file, transcribe_audio
from tts_backends import DEFAULT_BACKEND, available_backends, get_backend

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

# Showcase voice(s). Order = README display order.
# (Add more entries to clone additional voices — the matrix below expands automatically.)
CELEBRITIES = [
    {"id": "karina", "name": "카리나 (aespa)", "url": "https://www.youtube.com/watch?v=r96zEiIHVf4"},
]

# Three sample lines (Korean), mirroring notification_lines.json, used to
# compare how each TTS backend renders the same text.
CASES = [
    {
        "id": "done",
        "ko": "작업을 완료했습니다.",
        "en": "The task is complete.",
    },
    {
        "id": "permission",
        "ko": "실행 허가가 필요합니다.",
        "en": "Permission to run is required.",
    },
    {
        "id": "auth",
        "ko": "인증에 성공했습니다.",
        "en": "Authentication succeeded.",
    },
]

# Qwen3-TTS language code + text key in CASES + filename suffix.
# (Add {"code": "english", "key": "en", "suffix": "en"} to also generate English clips.)
LANGUAGES = [
    {"code": "korean", "key": "ko", "suffix": "ko"},
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


def prepare_reference(celeb: dict, device_info, need_ref_text: bool,
                      use_bgm_removal: bool = True) -> dict | None:
    """Run download -> (separate) -> segment (-> transcribe) for one celebrity.

    With `use_bgm_removal=False` the raw audio is used as the reference and a
    `_raw` filename suffix is applied, so BGM-removed and raw variants can be
    generated side by side for comparison.
    Transcription only runs for backends that need the transcript (`need_ref_text`).
    """
    cid = celeb["id"]
    variant = "" if use_bgm_removal else "_raw"
    logger.info(f"=== Preparing reference for {celeb['name']} ({cid}{variant}) ===")
    try:
        raw = download_audio(celeb["url"], cid)
        ref = separate_vocals(raw, cid, device_info) if use_bgm_removal else raw
        clip = auto_select_segment(ref, f"{cid}{variant}")
        ref_text = transcribe_audio(clip, device_info, language="ko") if need_ref_text else ""
        return {**celeb, "ref_audio": clip, "ref_text": ref_text, "suffix": variant}
    except Exception as e:
        logger.error(f"[{cid}] reference preparation failed: {e}")
        return None


def generate_samples(refs: list[dict], backend, device_info) -> list[Path]:
    """Clone every case/language in each prepared voice with the chosen backend."""
    SAMPLES_OUT_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    total = len(refs) * len(CASES) * len(LANGUAGES)
    logger.info(
        f"Generating {total} clips with '{backend.name}' "
        f"({len(refs)} voices x {len(CASES)} cases x {len(LANGUAGES)} langs)..."
    )

    outputs = []
    for ref in refs:
        cid = ref["id"]
        for case in CASES:
            for lang in LANGUAGES:
                text = case[lang["key"]]
                # Filename embeds the model so per-backend outputs can be compared.
                stem = f"{cid}_{case['id']}_{lang['suffix']}_{backend.name}"
                logger.info(f"[{stem}] cloning ({lang['suffix']}): {text}")
                audio, sr = backend.clone(text, ref["ref_audio"], ref["ref_text"], lang["suffix"])

                audio, sr = add_silence(audio, sr, silence_ms=300)
                out_path = SAMPLES_OUT_DIR / f"{stem}.wav"
                sf.write(str(out_path), audio, sr)

                asset_path = ASSETS_SAMPLES_DIR / f"{stem}.wav"
                shutil.copy(out_path, asset_path)
                logger.success(f"[{stem}] saved -> {asset_path.relative_to(PROJECT_ROOT)}")
                outputs.append(asset_path)

    return outputs


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate README voice samples")
    parser.add_argument("celebrities", nargs="*", help="Subset of celebrity ids (default: all)")
    parser.add_argument("--backend", default=DEFAULT_BACKEND,
                        help=f"TTS backend: {', '.join(available_backends())} (default: {DEFAULT_BACKEND})")
    parser.add_argument("--no-bgm-removal", action="store_true",
                        help="Skip Demucs BGM removal; use raw audio (adds a _raw suffix)")
    args = parser.parse_args()

    wanted = set(args.celebrities)
    celebs = [c for c in CELEBRITIES if not wanted or c["id"] in wanted]
    if not celebs:
        logger.error(f"No matching celebrities for {wanted}. Known: {[c['id'] for c in CELEBRITIES]}")
        sys.exit(1)

    logger.info(f"Backend: {args.backend} | Cases: {[c['id'] for c in CASES]} "
                f"| Languages: {[x['suffix'] for x in LANGUAGES]}")

    device_info = detect_device()
    print_device_info(device_info)

    backend = get_backend(args.backend)
    refs = [
        r for c in celebs
        if (r := prepare_reference(c, device_info, backend.needs_ref_text, not args.no_bgm_removal))
    ]
    if not refs:
        logger.error("No references prepared; aborting.")
        sys.exit(1)

    backend.load(device_info)
    outputs = generate_samples(refs, backend, device_info)

    logger.success(f"Done. {len(outputs)} samples in {ASSETS_SAMPLES_DIR.relative_to(PROJECT_ROOT)}/")
    for p in outputs:
        logger.info(f"  - {p.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
