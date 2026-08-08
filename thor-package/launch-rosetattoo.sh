#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${HOME}/Games/RoseTattoo"
GAME_DIR="${BASE_DIR}/game"
SAVE_DIR="${BASE_DIR}/saves"

mkdir -p "${SAVE_DIR}"

exec flatpak run org.scummvm.ScummVM \
  --fullscreen \
  --stretch-mode=pixel-perfect \
  --aspect-ratio \
  --savepath="${SAVE_DIR}" \
  --path="${GAME_DIR}" \
  sherlock:rosetattoo
