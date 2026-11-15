#!/bin/sh
# Run OpenCode headless against the remote LM Studio model (default) or local Ollama
# (OPENCODE_MODEL=ollama-local/qwen3.5-9b-32k) from the repo root.
#   tools/opencode/opencode-local.sh "Read tools/remaster/jobs.py and summarise recover()"
#   tools/opencode/opencode-local.sh --auto "Fix the failing test in tools/tests/test_x.py"
# Uses the project config next to this script; the global ~/.config/opencode is untouched.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
for candidate in "$(command -v opencode 2>/dev/null || true)" \
    /Applications/OpenChamber.app/Contents/Resources/opencode-cli/opencode \
    "$HOME/Library/Application Support/ai.opencode.desktop/cli"/*/opencode-cli; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then oc=$candidate; break; fi
done
: "${oc:?opencode not found; install OpenChamber or OpenCode}"
OPENCODE_CONFIG="$here/opencode.json" exec "$oc" run --standalone \
  -m "${OPENCODE_MODEL:-lmstudio-remote/qwen/qwen3.8-27b}" "$@"
