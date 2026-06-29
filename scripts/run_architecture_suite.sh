#!/usr/bin/env bash
# Task 9.9 — unified architecture verification (local + optional Docker E2E).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$ROOT/llama.cpp/build}"
MATRIX="${MATRIX:-$ROOT/config/architecture_matrix.json}"
REPORT="${REPORT:-$ROOT/logs/architecture_report.json}"
RUN_E2E="${RUN_E2E:-0}"
RUN_PLUGIN_TESTS="${RUN_PLUGIN_TESTS:-1}"

log() { printf '%s\n' "$*"; }

cpu_jobs() {
  if command -v nproc >/dev/null 2>&1; then nproc
  elif command -v sysctl >/dev/null 2>&1; then sysctl -n hw.ncpu
  else echo 4
  fi
}

DIST_BIN="$BUILD_DIR/bin"

cmake_build() {
  if [[ ! -d "$BUILD_DIR" ]]; then
    log "Configure: $BUILD_DIR"
    cmake -S "$ROOT/llama.cpp" -B "$BUILD_DIR" \
      -DCMAKE_BUILD_TYPE=Release \
      -DLLAMA_BUILD_TESTS=ON \
      -DGGML_METAL=OFF
  fi
  cmake --build "$BUILD_DIR" --target architecture-report -j"$(cpu_jobs)"
  if [[ "$RUN_PLUGIN_TESTS" == "1" ]]; then
    cmake --build "$BUILD_DIR" --target \
      test-llama-plugin test-qwen-plugin test-gemma-plugin \
      test-phi-plugin test-smol-plugin test-deepseek-plugin \
      test-runtime-descriptor test-install-idempotency -j"$(cpu_jobs)"
  fi
}

run_plugin_tests() {
  [[ "$RUN_PLUGIN_TESTS" != "1" ]] && return 0
  local tests=(
    test-llama-plugin test-qwen-plugin test-gemma-plugin
    test-phi-plugin test-smol-plugin test-deepseek-plugin
    test-runtime-descriptor test-install-idempotency
  )
  for t in "${tests[@]}"; do
    local bin="$DIST_BIN/$t"
    if [[ -x "$bin" ]]; then
      log "==> $t"
      "$bin" || [[ $? -eq 77 ]] || exit 1
    fi
  done
}

run_local_gguf_checks() {
  local bin="$DIST_BIN/architecture-report"
  if [[ -n "${MODEL:-}" && -f "${MODEL}" ]]; then
    log "==> architecture-report --gguf $MODEL"
    "$bin" --gguf "$MODEL" --output "$REPORT"
    return
  fi
  log "==> architecture-report --matrix $MATRIX"
  "$bin" --matrix "$MATRIX" --output "$REPORT"
}

run_docker_e2e() {
  [[ "$RUN_E2E" != "1" ]] && return 0
  log "==> Docker E2E (run_e2e_generate.py)"
  (
    cd "$ROOT/llama.cpp/tools/distributed/docker"
    ORCHESTRATOR="${ORCHESTRATOR:-http://127.0.0.1:9000}" python3 run_e2e_generate.py
  )
}

main() {
  log "Task 9.9 Architecture Verification Suite"
  log "  BUILD_DIR=$BUILD_DIR"
  log "  MATRIX=$MATRIX"
  log "  REPORT=$REPORT"
  cmake_build
  run_plugin_tests
  run_local_gguf_checks
  run_docker_e2e
  log "Done. Report: $REPORT"
}

main "$@"
