#!/usr/bin/env bash
# Full LAN benchmark — all catalog models on homelab 3-node cluster.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${BENCHMARK_PROFILE:-homelab_full}"
ORCHESTRATOR="${ORCHESTRATOR:-http://192.168.50.154:9000}"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${BENCHMARK_OUTPUT_DIR:-$ROOT/logs/benchmark/homelab_full_$RUN_ID}"
PROFILE_RUNTIME="${BENCHMARK_PROFILE_RUNTIME:-0}"

log() { printf '%s\n' "$*"; }

main() {
  cd "$ROOT"
  log "Homelab full benchmark"
  log "  profile=$PROFILE"
  log "  orchestrator=$ORCHESTRATOR"
  log "  output=$OUT_DIR"
  log "  profile_runtime=$PROFILE_RUNTIME"

  RUNTIME_ARGS=()
  if [[ "$PROFILE_RUNTIME" == "1" ]]; then
    RUNTIME_ARGS+=(--profile-runtime)
    export DIST_PERF_TRACE=1
    export DIST_PERF_TRACE_GGML=1
    export DIST_PERF_GPU_POLL_MS="${DIST_PERF_GPU_POLL_MS:-100}"
  fi

  BENCHMARK_DOCKER=0 \
  ORCHESTRATOR="$ORCHESTRATOR" \
  python3 benchmarks/benchmark_runner.py \
    --profile "$PROFILE" \
    --cluster-size 3 \
    --output-dir "$OUT_DIR" \
    "${RUNTIME_ARGS[@]}"

  REPORT_SRC="$OUT_DIR/report.md"
  REPORT_DST="$ROOT/docs/LAN_HOMELAB_BENCHMARK_REPORT_${RUN_ID}.md"
  if [[ -f "$REPORT_SRC" ]]; then
    cp "$REPORT_SRC" "$REPORT_DST"
    log "Copied report -> $REPORT_DST"
  fi

  PERF_REPORT="$OUT_DIR/perf_trace/analysis/report.md"
  if [[ -f "$PERF_REPORT" ]]; then
    PERF_DST="$ROOT/docs/LAN_HOMELAB_PERF_TRACE_${RUN_ID}.md"
    cp "$PERF_REPORT" "$PERF_DST"
    log "Copied perf trace report -> $PERF_DST"
  fi
}

main "$@"
