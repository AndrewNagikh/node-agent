#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/common.sh
source "$ROOT/scripts/common.sh"

usage() {
  cat <<EOF
usage: $0 [options]

Start the distributed orchestrator.

Environment:
  MODEL   path to GGUF model
  PORT    listen port (default: 9000)

Options:
  --model PATH
  --port PORT
  --build / --no-build
  -h, --help

Example:
  $0 --model ~/models/llama-3.2-1b-instruct-q4_k_m.gguf
EOF
}

MODEL="${MODEL:-}"
PORT="${PORT:-9000}"
DO_BUILD=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --port)  PORT="$2"; shift 2 ;;
    --build) DO_BUILD=true; shift ;;
    --no-build) DO_BUILD=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

BIN="$ROOT/llama.cpp/build/bin/orchestrator"
if [[ ! -x "$BIN" ]]; then
  if [[ "$DO_BUILD" == true ]]; then
    echo "run-orchestrator: building orchestrator ..."
    "$ROOT/build.sh" all
  else
    echo "run-orchestrator: $BIN not found" >&2
    exit 1
  fi
fi

MODEL="$(node_agent_find_model)"

echo "run-orchestrator: listen=0.0.0.0:$PORT model=$MODEL"
exec "$BIN" --model "$MODEL" --listen "0.0.0.0:${PORT}"
