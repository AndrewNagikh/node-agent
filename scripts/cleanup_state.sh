#!/usr/bin/env bash
# Prunes accumulated benchmark/trace state so it doesn't grow unbounded.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/common.sh
source "$ROOT/scripts/common.sh"

KEEP_RUNS="${KEEP_RUNS:-15}"
MAX_AGE_DAYS="${MAX_AGE_DAYS:-7}"
REMOTE=false
DRY_RUN=false

usage() {
  cat <<EOF
usage: $0 [options]

Prunes accumulated state so it doesn't grow unbounded:
  - logs/perf_trace/*_<timestamp>/ benchmark run directories in this repo
    (keeps the most recent KEEP_RUNS, default $KEEP_RUNS; never touches _baselines)
  - with --remote: also POSTs /perf/trace/cleanup (deletes raw perf-trace
    *.jsonl older than MAX_AGE_DAYS, default $MAX_AGE_DAYS) to the orchestrator
    and every node in nodes.conf, so you don't have to SSH/copy between
    machines to clean each one up by hand.

Options:
  --keep-runs N     local run dirs to keep (default: $KEEP_RUNS)
  --max-age-days N  remote raw-trace retention in days (default: $MAX_AGE_DAYS)
  --remote          also clean node_agent/orchestrator perf_trace state over HTTP
  --dry-run         print what would be deleted without deleting
  -h, --help

Examples:
  $0                          # local cleanup only
  $0 --remote                 # local + all cluster nodes
  $0 --remote --dry-run       # preview everything first
  $0 --keep-runs 5 --remote --max-age-days 3
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-runs)     KEEP_RUNS="$2"; shift 2 ;;
    --max-age-days)  MAX_AGE_DAYS="$2"; shift 2 ;;
    --remote)        REMOTE=true; shift ;;
    --dry-run)       DRY_RUN=true; shift ;;
    -h|--help)       usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

echo "=== local: logs/perf_trace run directories ==="
TRACE_DIR="$ROOT/logs/perf_trace"
if [[ -d "$TRACE_DIR" ]]; then
  RUN_DIRS=()
  while IFS= read -r dir; do
    RUN_DIRS+=("$dir")
  done < <(find "$TRACE_DIR" -maxdepth 1 -type d -regex '.*_[0-9]\{8\}_[0-9]\{6\}' | sort)

  total=${#RUN_DIRS[@]}
  if (( total > KEEP_RUNS )); then
    to_delete=$(( total - KEEP_RUNS ))
    echo "$total run dirs found, keeping $KEEP_RUNS most recent, removing $to_delete"
    for (( i = 0; i < to_delete; i++ )); do
      dir="${RUN_DIRS[$i]}"
      size="$(du -sh "$dir" 2>/dev/null | cut -f1)"
      if [[ "$DRY_RUN" == true ]]; then
        echo "  would remove: $dir ($size)"
      else
        echo "  removing: $dir ($size)"
        rm -rf "$dir"
      fi
    done
  else
    echo "$total run dirs found, within --keep-runs=$KEEP_RUNS, nothing to do"
  fi
else
  echo "$TRACE_DIR not found, skipping"
fi

if [[ "$REMOTE" != true ]]; then
  echo
  echo "(pass --remote to also clean node_agent/orchestrator perf_trace state on the cluster)"
  exit 0
fi

echo
echo "=== remote: /perf/trace/cleanup (max_age_days=$MAX_AGE_DAYS) ==="
node_agent_load_topology "$ROOT"

targets=()
if [[ -n "${ORCHESTRATOR_HOST:-}" ]]; then
  targets+=("orchestrator|http://${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT:-9000}")
fi
for node_id in node-a node-b node-c; do
  host="$(node_agent_topology_node_host "$node_id")"
  [[ -z "$host" ]] && continue
  port="$(node_agent_topology_node_port "$node_id")"
  targets+=("${node_id}|http://${host}:${port:-9001}")
done

if [[ ${#targets[@]} -eq 0 ]]; then
  echo "no nodes.conf topology found (ORCHESTRATOR_HOST/NODE_*_HOST unset), nothing to do"
  exit 0
fi

for t in "${targets[@]}"; do
  name="${t%%|*}"
  url="${t#*|}"
  if [[ "$DRY_RUN" == true ]]; then
    echo "  would POST $url/perf/trace/cleanup"
    continue
  fi
  resp="$(curl -sf -m 10 -X POST "$url/perf/trace/cleanup" \
    -H 'Content-Type: application/json' \
    -d "{\"max_age_days\": $MAX_AGE_DAYS}" 2>&1)" || {
    echo "  $name ($url): unreachable, skipping"
    continue
  }
  echo "  $name ($url): $resp"
done
