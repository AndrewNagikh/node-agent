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
  MODEL     optional path to local GGUF (legacy; layer-first mode omits this)
  PORT      listen port (default: 9000)
  HF_TOKEN  Hugging Face token (faster downloads; or ~/.cache/huggingface/token)

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
    "$ROOT/build.sh" orchestrator
  else
    echo "run-orchestrator: $BIN not found" >&2
    exit 1
  fi
fi

node_agent_ensure_hf_token "$ROOT"

ARGS=(--listen "0.0.0.0:${PORT}")
if [[ -n "$MODEL" ]]; then
  if [[ ! -f "$MODEL" ]]; then
    echo "run-orchestrator: MODEL not found: $MODEL" >&2
    exit 1
  fi
  ARGS+=(--model "$MODEL")
  echo "run-orchestrator: listen=0.0.0.0:$PORT model=$MODEL"
else
  echo "run-orchestrator: listen=0.0.0.0:$PORT (layer-first — no local MODEL)"
fi

exec "$BIN" "${ARGS[@]}"
