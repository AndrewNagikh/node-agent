#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/common.sh
source "$ROOT/scripts/common.sh"

usage() {
  cat <<EOF
usage: $0 [options]

Start a distributed node agent (auto-detects GPU at build time, benchmarks on startup).

Environment (optional):
  MODEL            path to GGUF model
  ORCHESTRATOR     orchestrator URL, e.g. http://192.168.50.154:9000
  NODE_ID          node-a | node-b | node-c  (default: node-a)
  PORT             HTTP listen port (default: by NODE_ID)
  ADVERTISE_HOST   LAN IP for other machines (auto-detected if unset)
  REBENCHMARK=1    force re-run benchmark
  HF_TOKEN         Hugging Face token (faster downloads; or ~/.cache/huggingface/token)

Options:
  --model PATH
  --orchestrator URL
  --node-id ID
  --port PORT
  --advertise-host IP
  --rebenchmark
  --build          build before run if binary missing (default)
  --no-build
  -h, --help

Examples:
  ORCHESTRATOR=http://192.168.50.154:9000 NODE_ID=node-b $0
  $0 --orchestrator http://192.168.50.154:9000 --node-id node-c --rebenchmark
EOF
}

MODEL="${MODEL:-}"
ORCHESTRATOR="${ORCHESTRATOR:-}"
NODE_ID="${NODE_ID:-node-a}"
PORT="${PORT:-}"
ADVERTISE_HOST="${ADVERTISE_HOST:-}"
REBENCHMARK="${REBENCHMARK:-false}"
DO_BUILD=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)           MODEL="$2"; shift 2 ;;
    --orchestrator)    ORCHESTRATOR="$2"; shift 2 ;;
    --node-id)         NODE_ID="$2"; shift 2 ;;
    --port)            PORT="$2"; shift 2 ;;
    --advertise-host)  ADVERTISE_HOST="$2"; shift 2 ;;
    --rebenchmark)     REBENCHMARK=true; shift ;;
    --build)           DO_BUILD=true; shift ;;
    --no-build)        DO_BUILD=false; shift ;;
    -h|--help)         usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$ORCHESTRATOR" ]]; then
  echo "run-agent: set --orchestrator or ORCHESTRATOR env" >&2
  usage
  exit 1
fi

if [[ "$DO_BUILD" == true ]]; then
  node_agent_ensure_built "$ROOT"
fi

MODEL="$(node_agent_find_model)"
PORT="${PORT:-$(node_agent_default_port "$NODE_ID")}"
ADVERTISE_HOST="$(node_agent_detect_lan_ip)"
node_agent_ensure_hf_token "$ROOT"

BIN="$ROOT/llama.cpp/build/bin/node_agent"
ARGS=(
  --model "$MODEL"
  --listen "0.0.0.0:${PORT}"
  --advertise-host "$ADVERTISE_HOST"
  --orchestrator "$ORCHESTRATOR"
  --node-id "$NODE_ID"
)

if [[ "$REBENCHMARK" == true || "$REBENCHMARK" == "1" ]]; then
  ARGS+=(--rebenchmark)
fi

echo "run-agent: node_id=$NODE_ID port=$PORT advertise=$ADVERTISE_HOST"
echo "run-agent: orchestrator=$ORCHESTRATOR"
echo "run-agent: model=$MODEL"

node_agent_wsl_portproxy_hint

exec "$BIN" "${ARGS[@]}"
