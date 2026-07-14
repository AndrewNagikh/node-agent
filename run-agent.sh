#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/common.sh
source "$ROOT/scripts/common.sh"

usage() {
  cat <<EOF
usage: $0 [NODE_ID=node-a] [KEY=value ...] [options]

Start a distributed node agent (auto-detects GPU at build time, benchmarks on startup).

With nodes.conf present (copy from nodes.conf.example and edit for your LAN),
the only thing you need per machine is the node id:

  ./run-agent.sh NODE_ID=node-a
  ./run-agent.sh NODE_ID=node-b
  ./run-agent.sh NODE_ID=node-c

KEY=value args (any can be omitted; falls back to nodes.conf, then env, then default):
  NODE_ID=node-a|node-b|node-c   (default: node-a)
  ORCHESTRATOR=http://host:9000
  PORT=9001
  ADVERTISE_HOST=192.168.1.10
  MODELS_DIR=/path/to/store
  MODEL=/path/to/model.gguf      (legacy; layer-first mode omits this)
  NODE_LOG=1                     (default; tees stdout/stderr to
                                   MODELS_DIR/logs/node_agent.log, rotated,
                                   fetchable remotely via GET /debug/log)
  REBENCHMARK=1
  VERIFY_MATERIALIZATION=1

Options (equivalent --flag form, still supported):
  --model PATH
  --orchestrator URL
  --node-id ID
  --port PORT
  --advertise-host IP
  --models-dir DIR
  --no-node-log    disable the persistent rotated log file
  --rebenchmark
  --verify-materialization
  --build          build before run if binary missing (default)
  --no-build
  -h, --help

Examples:
  ./run-agent.sh NODE_ID=node-b
  ./run-agent.sh NODE_ID=node-c REBENCHMARK=1
  ORCHESTRATOR=http://192.168.50.154:9000 NODE_ID=node-b $0
  $0 --orchestrator http://192.168.50.154:9000 --node-id node-c --rebenchmark
EOF
}

node_agent_load_topology "$ROOT"

MODEL="${MODEL:-}"
ORCHESTRATOR="${ORCHESTRATOR:-}"
NODE_ID="${NODE_ID:-node-a}"
PORT="${PORT:-}"
ADVERTISE_HOST="${ADVERTISE_HOST:-}"
MODELS_DIR="${MODELS_DIR:-}"
NODE_LOG="${NODE_LOG:-true}"
REBENCHMARK="${REBENCHMARK:-false}"
VERIFY_MATERIALIZATION="${VERIFY_MATERIALIZATION:-false}"
DO_BUILD=true

node_agent_parse_kv_args \
  "MODEL ORCHESTRATOR NODE_ID PORT ADVERTISE_HOST MODELS_DIR NODE_LOG REBENCHMARK VERIFY_MATERIALIZATION" \
  "$@"
# ${arr[@]} on a zero-element array throws "unbound variable" under `set -u`
# in bash 3.2 (macOS system bash) -- guard on length first.
if [[ ${#NODE_AGENT_KV_REMAINING[@]} -gt 0 ]]; then
  set -- "${NODE_AGENT_KV_REMAINING[@]}"
else
  set --
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)           MODEL="$2"; shift 2 ;;
    --orchestrator)    ORCHESTRATOR="$2"; shift 2 ;;
    --node-id)         NODE_ID="$2"; shift 2 ;;
    --port)            PORT="$2"; shift 2 ;;
    --advertise-host)  ADVERTISE_HOST="$2"; shift 2 ;;
    --models-dir)      MODELS_DIR="$2"; shift 2 ;;
    --no-node-log)     NODE_LOG=false; shift ;;
    --rebenchmark)     REBENCHMARK=true; shift ;;
    --verify-materialization) VERIFY_MATERIALIZATION=true; shift ;;
    --build)           DO_BUILD=true; shift ;;
    --no-build)        DO_BUILD=false; shift ;;
    -h|--help)         usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$ORCHESTRATOR" ]]; then
  ORCHESTRATOR="$(node_agent_topology_orchestrator_url)"
fi
if [[ -z "$ORCHESTRATOR" ]]; then
  echo "run-agent: set NODE_ID=... plus nodes.conf, or pass ORCHESTRATOR=http://host:9000" >&2
  usage
  exit 1
fi

if [[ "$DO_BUILD" == true ]]; then
  node_agent_ensure_built "$ROOT"
fi

PORT="${PORT:-$(node_agent_default_port "$NODE_ID")}"
if [[ -z "$ADVERTISE_HOST" ]]; then
  ADVERTISE_HOST="$(node_agent_topology_node_host "$NODE_ID")"
fi
ADVERTISE_HOST="$(node_agent_detect_lan_ip)"
node_agent_ensure_hf_token "$ROOT"
if [[ -z "$MODELS_DIR" ]]; then
  MODELS_DIR="$HOME/.distributed-llm/models"
fi
mkdir -p "$MODELS_DIR"

BIN="$ROOT/llama.cpp/build/bin/node_agent"
ARGS=(
  --listen "0.0.0.0:${PORT}"
  --advertise-host "$ADVERTISE_HOST"
  --orchestrator "$ORCHESTRATOR"
  --node-id "$NODE_ID"
  --models-dir "$MODELS_DIR"
)

if [[ -n "$MODEL" ]]; then
  if [[ ! -f "$MODEL" ]]; then
    echo "run-agent: MODEL not found: $MODEL" >&2
    exit 1
  fi
  ARGS+=(--model "$MODEL")
fi

if [[ "$REBENCHMARK" == true || "$REBENCHMARK" == "1" ]]; then
  ARGS+=(--rebenchmark)
fi

if [[ "$VERIFY_MATERIALIZATION" == true || "$VERIFY_MATERIALIZATION" == "1" ]]; then
  ARGS+=(--verify-materialization)
fi

echo "run-agent: node_id=$NODE_ID port=$PORT advertise=$ADVERTISE_HOST"
echo "run-agent: orchestrator=$ORCHESTRATOR"
if [[ -n "$MODEL" ]]; then
  echo "run-agent: model=$MODEL"
else
  echo "run-agent: layer-first mode (no local MODEL)"
fi
echo "run-agent: models_dir=$MODELS_DIR"
if [[ "$VERIFY_MATERIALIZATION" == true || "$VERIFY_MATERIALIZATION" == "1" ]]; then
  echo "run-agent: verify_materialization=on"
fi

node_agent_wsl_portproxy_hint

if [[ "$NODE_LOG" == true || "$NODE_LOG" == "1" ]]; then
  LOG_FILE="$MODELS_DIR/logs/node_agent.log"
  mkdir -p "$MODELS_DIR/logs"
  node_agent_rotate_log "$LOG_FILE"
  echo "run-agent: logging to $LOG_FILE (also GET /debug/log on this node's port)"
  # Redirect this shell's own fds through tee, then exec into $BIN -- the
  # exec below replaces the process image but keeps the fd redirection, so
  # $BIN still ends up as the direct, signal-addressable process (same PID)
  # instead of hiding behind a wrapper pipeline.
  exec > >(tee -a "$LOG_FILE") 2>&1
fi

exec "$BIN" "${ARGS[@]}"
