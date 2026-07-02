#!/usr/bin/env bash
# Task 10.0/10.1 — Cluster Benchmark Suite
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${BENCHMARK_PROFILE:-ci}"
MODE="${BENCHMARK_MODE:-}"
ORCHESTRATOR="${ORCHESTRATOR:-http://127.0.0.1:9000}"

log() { printf '%s\n' "$*"; }

main() {
  log "Cluster Benchmark Suite (profile=$PROFILE mode=$MODE)"
  cd "$ROOT"
  ORCHESTRATOR="$ORCHESTRATOR" python3 benchmarks/benchmark_runner.py --profile "$PROFILE" ${MODE:+--mode "$MODE"}
}

main "$@"
