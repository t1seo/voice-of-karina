#!/usr/bin/env python3
"""Generate the 3 Korean comparison samples with CosyVoice2.

Run from inside external/CosyVoice with its venv:

    cd external/CosyVoice
    PYTHONPATH="third_party/Matcha-TTS:." .venv/bin/python ../../scripts/gen_cosyvoice.py

Korean input skips zh/en text normalization (text_frontend=False), so
WeTextProcessing / pynini are not needed.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "third_party/Matcha-TTS")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import soundfile as sf

from cosyvoice.cli.cosyvoice import CosyVoice2

ROOT = Path("/Volumes/Nebula/Projects/karina-voice-notification")
REF = ROOT / "output/samples/clean/karina_clean.wav"
REF_TEXT = json.loads((ROOT / "output/transcripts/karina_clean_transcript.json").read_text())["text"]
OUT = ROOT / "assets/samples"
CASES = {
    "done": "작업을 완료했습니다.",
    "permission": "실행 허가가 필요합니다.",
    "auth": "인증에 성공했습니다.",
}

MODEL_DIR = os.environ.get("COSY_MODEL_DIR", "pretrained_models/CosyVoice2-0.5B")
model = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False, fp16=False)

for cid, text in CASES.items():
    # This CosyVoice2 build takes the prompt as a FILE PATH (it loads internally).
    for j in model.inference_zero_shot(text, REF_TEXT, str(REF), stream=False, text_frontend=False):
        out = OUT / f"karina_{cid}_ko_cosyvoice.wav"
        audio = j["tts_speech"].squeeze(0).cpu().numpy()  # (1, N) -> (N,)
        sf.write(str(out), audio, model.sample_rate)
        print("saved", out)
        break
