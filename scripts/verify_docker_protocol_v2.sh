#!/usr/bin/env bash
# RFC-0013 Phase 6 — verify v2 defaults + trace gates on 3-node Docker (Llama 3.2 1B).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER_DIR="$ROOT/llama.cpp/tools/distributed/docker"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${BENCHMARK_OUTPUT_DIR:-$ROOT/logs/perf_trace/rfc0013_docker_$RUN_ID}"
MODEL="${BENCHMARK_MODEL:-llama3_1b}"
LOG="$OUT_DIR/verify.log"

log() { printf '%s\n' "$*" | tee -a "$LOG" >&2; }

collect_traces() {
  local dest="$1/raw"
  mkdir -p "$dest"
  local found=0
  for ctr in dist-orchestrator dist-node-a dist-node-b dist-node-c; do
    if ! docker ps --format '{{.Names}}' | grep -qx "$ctr"; then
      log "WARN: container $ctr not running"
      continue
    fi
    while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      local base
      base="$(basename "$f")"
      docker cp "$ctr:$f" "$dest/${ctr}_${base}"
      found=$((found + 1))
      log "  collected $ctr:$f"
    done < <(docker exec "$ctr" sh -c 'find /data/models/perf_trace -name "*.jsonl" 2>/dev/null || true')
  done
  echo "$found"
}

validate_queue_overlap() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

queue_json = Path(sys.argv[1]) / "queue.json"
errors = []
if not queue_json.is_file():
    errors.append("missing queue.json")
    print("FAIL:", "; ".join(errors))
    sys.exit(1)

doc = json.loads(queue_json.read_text())
summary = doc.get("summary") or {}
for stage in ("entry", "middle", "final"):
    info = summary.get(stage)
    if not info:
        errors.append(f"missing queue summary for {stage}")
        continue
    if int(info.get("max", 0)) < 2:
        errors.append(f"{stage}_queue_depth max < 2: {info.get('max')}")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS queue overlap:", json.dumps(summary, indent=2))
PY
}

validate_analysis() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

analysis = Path(sys.argv[1])
trace = analysis / "trace.json"
queue = analysis / "queue.json"

errors = []
if not trace.is_file():
    errors.append("missing trace.json")
else:
    doc = json.loads(trace.read_text())
    if doc.get("event_count", 0) < 10:
        errors.append(f"too few events: {doc.get('event_count')}")
    queued = [ev for ev in doc.get("events", []) if ev.get("event") == "WAVE_QUEUED"]
    if not queued:
        errors.append("missing WAVE_QUEUED events")
    stages = {ev.get("stage") for ev in queued}
    for want in ("entry", "middle", "final"):
        if want not in stages:
            errors.append(f"missing WAVE_QUEUED stage: {want}")

if not queue.is_file():
    errors.append("missing queue.json")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS analysis:", json.loads(trace.read_text()).get("event_count"), "events")
PY
}

validate_bubble() {
  python3 - <<'PY' "$1" "$2"
import json, sys
from pathlib import Path

out_dir = Path(sys.argv[1])
benchmarks_root = Path(sys.argv[2])
raw = out_dir / "raw"
results_path = out_dir / "benchmark" / "results.json"
if not results_path.is_file():
    print("FAIL: missing benchmark/results.json for bubble gate")
    sys.exit(1)

trace_id = None
doc = json.loads(results_path.read_text())
for scenario in doc.get("scenarios", []):
    for stage in scenario.get("stages", []):
        if stage.get("name") != "generate":
            continue
        metrics = stage.get("metrics") or {}
        trace_id = metrics.get("trace_id")
        timing = metrics.get("timing") or {}
        if not trace_id:
            trace_id = timing.get("trace_id")
        if trace_id:
            break
    if trace_id:
        break
if not trace_id:
    print("FAIL: no trace_id in benchmark generate stage")
    sys.exit(1)

sys.path.insert(0, str(benchmarks_root / "benchmarks"))
from perf_trace.pipeline_stall_analysis import analyze_trace

stall = analyze_trace(raw, trace_id)
if not stall:
    print("FAIL: bubble analysis found no decode session")
    sys.exit(1)

avg_period = float(stall.get("avg_entry_period_ms") or 0)
avg_bubble = float(stall.get("avg_bubble_ms") or 0)
bubble_pct = (100.0 * avg_bubble / avg_period) if avg_period > 0 else 100.0
print(f"bubble: {avg_bubble:.1f}ms avg, period {avg_period:.1f}ms, share {bubble_pct:.1f}%")
if bubble_pct >= 75.0:
    print(f"FAIL bubble_pct={bubble_pct:.1f}% (regression vs v1 ~75% gate)")
    sys.exit(1)
if bubble_pct >= 10.0:
    print(f"WARN bubble_pct={bubble_pct:.1f}% (Phase 5 target <10%; Docker CPU+embedding may dominate)")
print(f"PASS bubble gate: {bubble_pct:.1f}% < 75% regression threshold")
PY
}

main() {
  mkdir -p "$OUT_DIR"
  : > "$LOG"

  log "RFC-0013 Docker verification (model=$MODEL)"
  log "  output=$OUT_DIR"

  log "==> docker compose build"
  (cd "$DOCKER_DIR" && docker compose build) 2>&1 | tee -a "$LOG"

  log "==> clear prior perf trace volume"
  docker volume rm dist-llm_dist-perf-trace 2>/dev/null || true

  log "==> docker compose up (v2 defaults from compose file)"
  (cd "$DOCKER_DIR" && \
    DIST_PERF_TRACE=1 \
    DIST_PERF_TRACE_GGML=1 \
    docker compose up -d --force-recreate) 2>&1 | tee -a "$LOG"

  log "==> smoke"
  (cd "$DOCKER_DIR" && ORCH_URL=http://127.0.0.1:9000 ./smoke.sh) 2>&1 | tee -a "$LOG"
  log "==> wait for pipeline workers to load model"
  sleep 20

  log "==> quick generate gate (v2 defaults, llama3_1b)"
  quick_ok=0
  for attempt in 1 2 3 4 5; do
    SESSION=$(curl -sf http://127.0.0.1:9000/session/create -H 'Content-Type: application/json' \
      -d '{"model":"llama-3.2-1b","n_ctx":512}' | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true)
    if [[ -z "$SESSION" ]]; then
      log "  attempt $attempt: session create failed, retrying..."
      sleep 10
      continue
    fi
    GEN_JSON=$(curl -m 120 -s http://127.0.0.1:9000/session/generate -H 'Content-Type: application/json' \
      -d "{\"session_id\":\"$SESSION\",\"prompt\":\"Hello\",\"max_tokens\":8}" || true)
    if GEN_JSON="$GEN_JSON" python3 - <<'PY' 2>/dev/null | tee -a "$LOG"
import json, os, sys
raw = os.environ.get("GEN_JSON", "").strip()
if not raw:
    sys.exit(1)
doc = json.loads(raw)
count = int(doc.get("count", len(doc.get("tokens", []))) or 0)
if count < 1:
    sys.exit(1)
print(f"PASS quick generate: count={count} tps={doc.get('timing',{}).get('decode_tokens_per_sec')}")
PY
    then
      quick_ok=1
      break
    fi
    err_hint=$(echo "$GEN_JSON" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print(d.get("error","empty"))' 2>/dev/null || echo empty)
    log "  attempt $attempt: generate not ready ($err_hint), retrying..."
    sleep 10
  done
  if [[ "$quick_ok" -ne 1 ]]; then
    log "FAIL: quick generate gate after retries"
    exit 1
  fi

  log "==> benchmark (rfc0013_docker + --profile-runtime)"
  cd "$ROOT"
  ORCHESTRATOR=http://127.0.0.1:9000 \
  BENCHMARK_DOCKER=1 \
  BENCHMARK_SKIP_DOCKER_RECREATE=1 \
  DIST_PERF_TRACE=1 \
  DIST_PERF_TRACE_GGML=1 \
  python3 benchmarks/benchmark_runner.py \
    --profile rfc0013_docker \
    --model "$MODEL" \
    --cluster-size 3 \
    --profile-runtime \
    --output-dir "$OUT_DIR/benchmark" 2>&1 | tee -a "$LOG"

  log "==> collect traces"
  n_files="$(collect_traces "$OUT_DIR")"
  log "  collected $n_files jsonl files"
  if [[ "$n_files" -lt 1 ]]; then
    log "FAIL: no perf trace jsonl in containers"
    exit 1
  fi

  log "==> merge + postprocess"
  PYTHONPATH="$ROOT/benchmarks" python3 "$ROOT/benchmarks/perf_trace/postprocess.py" \
    --raw "$OUT_DIR/raw" \
    --out "$OUT_DIR" \
    --profile rfc0013_docker \
    --model "$MODEL" \
    --cluster-size 3 \
    --baseline-dir "$ROOT/logs/perf_trace/_baselines" \
    --pin-if-missing 2>&1 | tee -a "$LOG"

  log "==> validate"
  validate_analysis "$OUT_DIR/analysis" 2>&1 | tee -a "$LOG"
  validate_queue_overlap "$OUT_DIR/analysis" 2>&1 | tee -a "$LOG"
  validate_bubble "$OUT_DIR" "$ROOT" 2>&1 | tee -a "$LOG"

  log "==> v1 rollback smoke (DIST_RUNTIME_PROTOCOL_V2=0)"
  (cd "$DOCKER_DIR" && \
    DIST_RUNTIME_PROTOCOL_V2=0 \
    DIST_RUNTIME_ENTRY_QUEUE=0 \
    DIST_RUNTIME_STAGE_QUEUE=0 \
    DIST_RUNTIME_CLIENT_PIPELINE=0 \
    DIST_PERF_TRACE=0 \
    docker compose up -d --force-recreate) 2>&1 | tee -a "$LOG"
  sleep 20
  V1_SESSION=$(curl -sf http://127.0.0.1:9000/session/create -H 'Content-Type: application/json' \
    -d '{"model":"llama-3.2-1b","n_ctx":512}' | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id",""))' || true)
  if [[ -z "$V1_SESSION" ]]; then
    log "FAIL: v1 rollback session create"
    exit 1
  fi
  V1_JSON=$(curl -m 120 -s http://127.0.0.1:9000/session/generate -H 'Content-Type: application/json' \
    -d "{\"session_id\":\"$V1_SESSION\",\"prompt\":\"Hi\",\"max_tokens\":4}" || true)
  if ! V1_JSON="$V1_JSON" python3 - <<'PY' 2>/dev/null | tee -a "$LOG"
import json, os, sys
raw = os.environ.get("V1_JSON", "").strip()
doc = json.loads(raw) if raw else {}
count = int(doc.get("count", len(doc.get("tokens", []))) or 0)
if count < 1:
    print("FAIL v1 rollback generate:", doc.get("error"))
    sys.exit(1)
print(f"PASS v1 rollback generate: count={count}")
PY
  then
    log "FAIL: v1 rollback generate"
    exit 1
  fi
  if ! docker logs dist-node-a 2>&1 | grep -q "DEPRECATED v1 RPC"; then
    log "WARN: expected v1 deprecation log on node-a (non-fatal)"
  else
    log "PASS v1 deprecation log present"
  fi

  log "Done. Artifacts: $OUT_DIR"
}

main "$@"
