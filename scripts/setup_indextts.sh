#!/usr/bin/env bash
set -euo pipefail
ROOT="/Volumes/Nebula/Projects/karina-voice-notification"
UV="$ROOT/.pixi/envs/default/bin/uv"
cd "$ROOT/external/index-tts"
echo "=== uv sync (no cuda extras) ==="
"$UV" sync
echo "=== download checkpoints (IndexTeam/IndexTTS-2) ==="
"$UV" run python -c "from huggingface_hub import snapshot_download; snapshot_download('IndexTeam/IndexTTS-2', local_dir='checkpoints')"
echo "=== checkpoint size ==="; du -sh checkpoints
echo "INDEXTTS_SETUP_DONE"
