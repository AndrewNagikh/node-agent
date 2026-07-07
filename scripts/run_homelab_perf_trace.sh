#!/usr/bin/env bash
# Task 12 — homelab perf trace smoke (runtime_profile, single-model friendly).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORCHESTRATOR="${ORCHESTRATOR:-http://192.168.50.154:9000}"
PROFILE="${BENCHMARK_PROFILE:-runtime_profile}"
MODEL="${BENCHMARK_MODEL:-tinyllama}"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${BENCHMARK_OUTPUT_DIR:-$ROOT/logs/perf_trace/homelab_verify_$RUN_ID}"

log() { printf '%s\n' "$*"; }

main() {
  cd "$ROOT"
  log "Task 12 homelab perf trace verification"
  log "  orchestrator=$ORCHESTRATOR"
  log "  profile=$PROFILE model=$MODEL"
  log "  output=$OUT_DIR"

  BENCHMARK_DOCKER=0 \
  ORCHESTRATOR="$ORCHESTRATOR" \
  DIST_PERF_TRACE=1 \
  DIST_PERF_TRACE_GGML=1 \
  DIST_PERF_GPU_POLL_MS="${DIST_PERF_GPU_POLL_MS:-100}" \
  python3 benchmarks/benchmark_runner.py \
    --profile "$PROFILE" \
    --model "$MODEL" \
    --cluster-size 3 \
    --profile-runtime \
    --output-dir "$OUT_DIR"

  log "Done. Artifacts: $OUT_DIR"
  log "  timeline: $OUT_DIR/perf_trace/analysis/timeline.html"
}

main "$@"
