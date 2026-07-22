#!/usr/bin/env bash
set -uo pipefail
ROOT="/Volumes/Nebula/Projects/karina-voice-notification"
UV="$ROOT/.pixi/envs/default/bin/uv"
cd "$ROOT/external/CosyVoice"
OFST="$(brew --prefix openfst)"
export CPPFLAGS="-I$OFST/include ${CPPFLAGS:-}"
export LDFLAGS="-L$OFST/lib ${LDFLAGS:-}"

echo "=== create venv (py3.10) ==="
"$UV" venv --python 3.10 .venv
PY="$ROOT/external/CosyVoice/.venv/bin/python"
PIP() { "$UV" pip install --python "$PY" "$@"; }

echo "=== pynini (needs openfst) ==="
PIP "pynini==2.1.6" 2>&1 | tail -5 || { echo "pynini 2.1.6 failed, trying 2.1.5"; PIP "pynini==2.1.5" 2>&1 | tail -5; }
echo "=== WeTextProcessing / wetext ==="
PIP "WeTextProcessing==1.0.3" 2>&1 | tail -3 || PIP "wetext" 2>&1 | tail -3
echo "=== requirements.txt ==="
PIP -r requirements.txt 2>&1 | tail -8
echo "=== modelscope + download CosyVoice2-0.5B ==="
PIP modelscope 2>&1 | tail -2
"$PY" -c "from modelscope import snapshot_download; snapshot_download('iic/CosyVoice2-0.5B', local_dir='pretrained_models/CosyVoice2-0.5B')" 2>&1 | tail -5
echo "=== import test ==="
cd "$ROOT/external/CosyVoice"
PYTHONPATH="third_party/Matcha-TTS:." "$PY" -c "from cosyvoice.cli.cosyvoice import CosyVoice2; print('COSY_IMPORT_OK')" 2>&1 | tail -8
echo "COSY_SETUP_DONE"
