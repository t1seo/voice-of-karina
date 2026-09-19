#!/bin/sh
set -eu

skill_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
project_root=$(CDPATH= cd -- "$skill_dir/../../.." && pwd -P)
export VOICE_OF_KARINA_HOME="${VOICE_OF_KARINA_HOME:-$project_root/.voice-of-karina}"

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'voice-of-karina requires uv: https://docs.astral.sh/uv/getting-started/installation/' >&2
    exit 127
fi

exec uv run --project "$project_root" --extra local python -m voice_of_karina "$@"
