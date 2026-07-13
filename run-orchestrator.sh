#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/common.sh
source "$ROOT/scripts/common.sh"

usage() {
  cat <<EOF
usage: $0 [KEY=value ...] [options]

Start the distributed orchestrator. With nodes.conf present, no arguments
are needed at all -- the listen port comes from ORCHESTRATOR_PORT there.

Environment / KEY=value args:
  MODEL     optional path to local GGUF (legacy; layer-first mode omits this)
  PORT      listen port (default: nodes.conf ORCHESTRATOR_PORT, else 9000)
  HF_TOKEN  Hugging Face token (faster downloads; or ~/.cache/huggingface/token)

Options (equivalent --flag form, still supported):
  --model PATH
  --port PORT
  --build / --no-build
  -h, --help

Examples:
  ./run-orchestrator.sh
  ./run-orchestrator.sh PORT=9000
  $0 --model ~/models/llama-3.2-1b-instruct-q4_k_m.gguf
EOF
}

node_agent_load_topology "$ROOT"

MODEL="${MODEL:-}"
PORT="${PORT:-${ORCHESTRATOR_PORT:-9000}}"
DO_BUILD=true

node_agent_parse_kv_args "MODEL PORT" "$@"
# ${arr[@]} on a zero-element array throws "unbound variable" under `set -u`
# in bash 3.2 (macOS system bash) -- guard on length first.
if [[ ${#NODE_AGENT_KV_REMAINING[@]} -gt 0 ]]; then
  set -- "${NODE_AGENT_KV_REMAINING[@]}"
else
  set --
fi

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
