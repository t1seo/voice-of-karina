#!/usr/bin/env bash
# Convert the generated sample .wav files into labelled waveform .mp4 videos.
#
# Why videos? GitHub renders an inline <video> player for uploaded video
# attachments, but strips <audio> tags and never plays audio files inline.
# Wrapping each clip in a waveform video is the standard workaround for
# "playable audio in a README": the play button plays our actual wav audio.
#
# Output: output/samples/video/<stem>.mp4  (same stem as the wav)
# (Written for macOS's stock bash 3.2 — no associative arrays.)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/assets/samples"
OUT="$ROOT/output/samples/video"
FONT="/System/Library/Fonts/Supplemental/Arial.ttf"
mkdir -p "$OUT"

# Pick an ffmpeg that actually has the drawtext filter (Homebrew's often doesn't;
# the conda/pixi build does).
FFMPEG="${FFMPEG:-ffmpeg}"
if ! "$FFMPEG" -hide_banner -filters 2>/dev/null | grep -qw drawtext; then
  for cand in "$ROOT/.pixi/envs/default/bin/ffmpeg" "$ROOT/.pixi/envs/mac/bin/ffmpeg"; do
    if [ -x "$cand" ] && "$cand" -hide_banner -filters 2>/dev/null | grep -qw drawtext; then
      FFMPEG="$cand"; break
    fi
  done
fi

name_of() { case "$1" in
  karina) echo "Karina";; chaeyoung) echo "Chae-young";; eunbin) echo "Eun-bin";; *) echo "$1";;
esac; }
case_of() { case "$1" in
  done) echo "Task complete";; permission) echo "Permission";; auth) echo "Auth success";; *) echo "$1";;
esac; }
model_of() { case "$1" in
  chatterbox) echo "Chatterbox";; qwen3) echo "Qwen3-TTS";; indextts2) echo "IndexTTS-2";;
  cosyvoice) echo "CosyVoice";; "") echo "";; *) echo "$1";;
esac; }

only="${1:-}"   # optional stem filter, e.g. karina_done_ko_chatterbox

for wav in "$SRC"/*.wav; do
  stem="$(basename "$wav" .wav)"          # e.g. karina_done_ko_chatterbox
  [ -n "$only" ] && [ "$stem" != "$only" ] && continue
  # Stem layout: <who>_<case>_<lang>_<model>
  IFS='_' read -r who what lang model <<< "$stem"
  m="$(model_of "$model")"
  label="$(name_of "$who")  |  $(case_of "$what")${m:+  |  $m}"
  out="$OUT/$stem.mp4"

  "$FFMPEG" -y -loglevel error -i "$wav" -filter_complex \
    "[0:a]showwaves=s=760x180:mode=cline:rate=30:colors=0x22d3ee|0x818cf8[w];\
     [w]drawtext=fontfile=${FONT}:text='${label}':x=24:y=22:fontsize=26:fontcolor=white:\
box=1:boxcolor=0x0d1117@0.75:boxborderw=10[v]" \
    -map "[v]" -map 0:a -c:v libx264 -pix_fmt yuv420p -preset veryfast \
    -c:a aac -b:a 128k -movflags +faststart -shortest "$out"
  echo "made: output/samples/video/$stem.mp4"
done
