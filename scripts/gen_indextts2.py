#!/usr/bin/env python3
"""Generate the 3 Korean comparison samples with IndexTTS-2.

Run from inside external/index-tts (so ./checkpoints resolves), using that
project's uv venv:

    cd external/index-tts
    PYTHONPATH=. .venv/bin/python ../../scripts/gen_indextts2.py
"""
import os
from pathlib import Path

from indextts.infer_v2 import IndexTTS2

ROOT = Path("/Volumes/Nebula/Projects/karina-voice-notification")
REF = ROOT / "output/samples/clean/karina_clean.wav"
OUT = ROOT / "assets/samples"
CASES = {
    "done": "작업을 완료했습니다.",
    "permission": "실행 허가가 필요합니다.",
    "auth": "인증에 성공했습니다.",
}

# Let unsupported MPS ops fall back to CPU instead of crashing.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

tts = IndexTTS2(
    cfg_path="checkpoints/config.yaml",
    model_dir="checkpoints",
    use_fp16=False,
    use_cuda_kernel=False,
    use_deepspeed=False,
)

for cid, text in CASES.items():
    out = OUT / f"karina_{cid}_ko_indextts2.wav"
    tts.infer(spk_audio_prompt=str(REF), text=text, output_path=str(out), verbose=True)
    print("saved", out)
