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
  MODEL       optional path to local GGUF (legacy; layer-first mode omits this)
  PORT        listen port (default: nodes.conf ORCHESTRATOR_PORT, else 9000)
  MODELS_DIR  state dir (default: ~/.distributed-llm/models)
  NODE_LOG=1  (default; tees stdout/stderr to MODELS_DIR/logs/orchestrator.log,
               rotated, fetchable remotely via GET /debug/log)
  HF_TOKEN    Hugging Face token (faster downloads; or ~/.cache/huggingface/token)

Options (equivalent --flag form, still supported):
  --model PATH
  --port PORT
  --models-dir DIR
  --no-node-log    disable the persistent rotated log file
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
MODELS_DIR="${MODELS_DIR:-}"
NODE_LOG="${NODE_LOG:-true}"
DO_BUILD=true

node_agent_parse_kv_args "MODEL PORT MODELS_DIR NODE_LOG" "$@"
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
    --models-dir) MODELS_DIR="$2"; shift 2 ;;
    --no-node-log) NODE_LOG=false; shift ;;
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

if [[ -z "$MODELS_DIR" ]]; then
  MODELS_DIR="$HOME/.distributed-llm/models"
fi
mkdir -p "$MODELS_DIR"

ARGS=(--listen "0.0.0.0:${PORT}" --models-dir "$MODELS_DIR")
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
echo "run-orchestrator: models_dir=$MODELS_DIR"

if [[ "$NODE_LOG" == true || "$NODE_LOG" == "1" ]]; then
  LOG_FILE="$MODELS_DIR/logs/orchestrator.log"
  mkdir -p "$MODELS_DIR/logs"
  node_agent_rotate_log "$LOG_FILE"
  echo "run-orchestrator: logging to $LOG_FILE (also GET /debug/log on this port)"
  exec > >(tee -a "$LOG_FILE") 2>&1
fi

exec "$BIN" "${ARGS[@]}"
