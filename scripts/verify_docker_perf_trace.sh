#!/usr/bin/env bash
# Task 12 — verify perf trace on 3-node Docker cluster.
# Rebuilds cluster, runs tinyllama benchmark with --profile-runtime,
# collects JSONL from all containers, merges, validates.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER_DIR="$ROOT/llama.cpp/tools/distributed/docker"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${BENCHMARK_OUTPUT_DIR:-$ROOT/logs/perf_trace/docker_verify_$RUN_ID}"
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

validate_analysis() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

analysis = Path(sys.argv[1])
trace = analysis / "trace.json"
bottleneck = analysis / "bottleneck.json"
tokens = analysis / "tokens.csv"

errors = []
if not trace.is_file():
    errors.append("missing trace.json")
else:
    doc = json.loads(trace.read_text())
    if doc.get("event_count", 0) < 5:
        errors.append(f"too few events: {doc.get('event_count')}")
    cats = (doc.get("bottleneck") or {}).get("category_pct", {})
    if not cats:
        errors.append("empty bottleneck categories")
    stages = {ev.get("stage") for ev in doc.get("events", []) if ev.get("kind") == "span"}
    for want in ("entry", "middle", "final"):
        if want not in stages:
            errors.append(f"missing stage spans: {want}")

if not bottleneck.is_file():
    errors.append("missing bottleneck.json")
if not tokens.is_file():
    errors.append("missing tokens.csv")
queue_csv = analysis / "queue.csv"
if not queue_csv.is_file():
    errors.append("missing queue.csv")
else:
    doc = json.loads(trace.read_text()) if trace.is_file() else {}
    decode_events = [ev for ev in doc.get("events", []) if ev.get("phase") == "decode"]
    queue_events = [ev for ev in decode_events if ev.get("event") == "QUEUE_DEPTH"]
    if not queue_events:
        errors.append("missing QUEUE_DEPTH events")
    else:
        stages = {ev.get("stage") for ev in queue_events}
        for want in ("entry", "middle", "final"):
            if want not in stages:
                errors.append(f"missing QUEUE_DEPTH stage: {want}")
    decode_spans = {ev.get("event") for ev in decode_events if ev.get("kind") == "span"}
    for want in ("ENTRY_COMPUTE_END", "MIDDLE_COMPUTE_END", "FINAL_COMPUTE_END", "HIDDEN_TRANSFER"):
        if want not in decode_spans:
            errors.append(f"missing decode span: {want}")
    receive_events = {ev.get("event") for ev in decode_events if ev.get("kind") == "instant"}
    for want in ("ENTRY_RECEIVE", "MIDDLE_RECEIVE", "FINAL_RECEIVE"):
        if want not in receive_events:
            errors.append(f"missing decode instant: {want}")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS:", json.dumps({
    "events": json.loads(trace.read_text()).get("event_count"),
    "tokens": json.loads(trace.read_text()).get("token_count"),
    "bottleneck": json.loads(bottleneck.read_text()).get("category_pct"),
}, indent=2))
PY
}

validate_install() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

analysis = Path(sys.argv[1])
install = analysis / "install.json"
reuse = analysis / "install_reuse.json"

errors = []
if not install.is_file():
    errors.append("missing install.json")
else:
    doc = json.loads(install.read_text())
    if doc.get("event_count", 0) < 1:
        errors.append(f"too few install events: {doc.get('event_count')}")
    events = doc.get("events", [])
    has_plan = any(ev.get("event", "").startswith("INSTALL_PLAN") for ev in events)
    has_blob_or_reuse = any(
        ev.get("event") in ("INSTALL_BLOB", "INSTALL_FULL_REUSE")
        for ev in events
    )
    if not has_plan and not has_blob_or_reuse:
        errors.append("missing INSTALL_PLAN or INSTALL_BLOB events")

if not reuse.is_file():
    errors.append("missing install_reuse.json")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS install:", json.dumps({
    "events": json.loads(install.read_text()).get("event_count"),
    "reuse": json.loads(reuse.read_text()),
}, indent=2))
PY
}

validate_session() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

analysis = Path(sys.argv[1])
session = analysis / "session.json"

errors = []
if not session.is_file():
    errors.append("missing session.json")
else:
    doc = json.loads(session.read_text())
    if doc.get("span_count", 0) < 3:
        errors.append(f"too few session spans: {doc.get('span_count')}")
    events = {ev.get("event") for ev in doc.get("events", []) if ev.get("kind") == "span"}
    required = {"SESSION_CONFIGURE_NODE", "SESSION_READY_WAIT"}
    if not required.issubset(events):
        errors.append(f"missing spans: {sorted(required - events)}")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS session:", json.dumps({
    "spans": json.loads(session.read_text()).get("span_count"),
    "breakdown": json.loads(session.read_text()).get("breakdown", {}).get("event_pct", {}),
}, indent=2))
PY
}

validate_ttft() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

analysis = Path(sys.argv[1])
ttft = analysis / "ttft.json"

errors = []
if not ttft.is_file():
    errors.append("missing ttft.json")
else:
    doc = json.loads(ttft.read_text())
    events = doc.get("events", [])
    if doc.get("event_count", 0) < 3:
        errors.append(f"too few ttft events: {doc.get('event_count')}")
    has_client = any(ev.get("event") == "CLIENT_TTFT" for ev in events)
    if not has_client:
        errors.append("missing CLIENT_TTFT")
    stages = {ev.get("stage") for ev in events if ev.get("phase") == "ttft" and ev.get("kind") == "span"}
    for want in ("entry", "middle", "final"):
        if want not in stages:
            errors.append(f"missing ttft stage: {want}")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS ttft:", json.dumps({
    "events": json.loads(ttft.read_text()).get("event_count"),
    "summary": json.loads(ttft.read_text()).get("summary", {}),
}, indent=2))
PY
}

validate_gpu() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

analysis = Path(sys.argv[1])
gpu = analysis / "gpu.json"

errors = []
if not gpu.is_file():
    errors.append("missing gpu.json")
else:
    doc = json.loads(gpu.read_text())
    if doc.get("sample_count", 0) < 3:
        errors.append(f"too few GPU samples: {doc.get('sample_count')}")
    backends = (doc.get("summary") or {}).get("backends", {})
    if not backends:
        errors.append("missing GPU backend summary")
    nodes = (doc.get("summary") or {}).get("by_node", {})
    if len(nodes) < 1:
        errors.append("missing per-node GPU stats")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS gpu:", json.dumps({
    "samples": json.loads(gpu.read_text()).get("sample_count"),
    "summary": json.loads(gpu.read_text()).get("summary", {}),
}, indent=2))
PY
}

validate_ggml() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

analysis = Path(sys.argv[1])
ggml = analysis / "ggml.json"

errors = []
required = {"GGML_GRAPH_EXECUTE", "SCHED_QUEUE_WAIT"}
if not ggml.is_file():
    errors.append("missing ggml.json")
else:
    doc = json.loads(ggml.read_text())
    if doc.get("span_count", 0) < 3:
        errors.append(f"too few ggml spans: {doc.get('span_count')}")
    counts = (doc.get("summary") or {}).get("event_counts", {})
    for want in sorted(required):
        if counts.get(want, 0) < 1:
            errors.append(f"missing ggml span: {want}")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS ggml:", json.dumps({
    "spans": json.loads(ggml.read_text()).get("span_count"),
    "summary": json.loads(ggml.read_text()).get("summary", {}),
}, indent=2))
PY
}

validate_budget() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

analysis = Path(sys.argv[1])
budget = analysis / "budget.json"
report = analysis / "report.md"
bottleneck = analysis / "bottleneck.json"

errors = []
if not budget.is_file():
    errors.append("missing budget.json")
else:
    doc = json.loads(budget.read_text())
    counts = doc.get("status_counts") or {}
    evaluated = counts.get("PASS", 0) + counts.get("WARN", 0) + counts.get("FAIL", 0)
    if evaluated < 3:
        errors.append(f"too few budget evaluations: {evaluated}")
    rollup = (doc.get("metrics") or {}).get("rollup", {})
    if not rollup.get("buckets_pct"):
        errors.append("missing decode bucket rollup")
if not report.is_file():
    errors.append("missing report.md")
if not bottleneck.is_file():
    errors.append("missing bottleneck.json")
elif "budget" not in json.loads(bottleneck.read_text()):
    errors.append("bottleneck.json missing budget section")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS budget:", json.dumps({
    "status_counts": json.loads(budget.read_text()).get("status_counts"),
    "failures": [r for r in json.loads(budget.read_text()).get("budget", []) if r.get("status") == "FAIL"],
}, indent=2))
PY
}

validate_regression() {
  python3 - <<'PY' "$1"
import json, sys
from pathlib import Path

analysis = Path(sys.argv[1])
regression = analysis / "regression_diff.json"
report = analysis / "regression.md"

errors = []
if not regression.is_file():
    errors.append("missing regression_diff.json")
else:
    doc = json.loads(regression.read_text())
    if not doc.get("comparisons"):
        errors.append("empty regression comparisons")
    summary = doc.get("summary") or {}
    if not doc.get("baseline_pinned") and summary.get("has_critical_fail"):
        errors.append("critical regression FAIL on decode/TTFT")
if not report.is_file():
    errors.append("missing regression.md")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS regression:", json.dumps({
    "baseline_pinned": json.loads(regression.read_text()).get("baseline_pinned"),
    "summary": json.loads(regression.read_text()).get("summary"),
}, indent=2))
PY
}

validate_timeline() {
  python3 - <<'PY' "$1"
import sys
from pathlib import Path

analysis = Path(sys.argv[1])
timeline = analysis / "timeline.html"
meta = analysis / "timeline.json"

errors = []
if not timeline.is_file():
    errors.append("missing timeline.html")
else:
    text = timeline.read_text(encoding="utf-8", errors="replace")
    for needle in ("TTFT Timeline", "Decode Timeline", "GPU"):
        if needle not in text:
            errors.append(f"timeline.html missing section: {needle}")
if not meta.is_file():
    errors.append("missing timeline.json")

if errors:
    print("FAIL:", "; ".join(errors))
    sys.exit(1)
print("PASS timeline:", meta.read_text(encoding="utf-8") if meta.is_file() else "{}")
PY
}

main() {
  mkdir -p "$OUT_DIR"
  : > "$LOG"

  log "Task 12 Docker perf trace verification"
  log "  output=$OUT_DIR"

  log "==> docker compose build"
  (cd "$DOCKER_DIR" && docker compose build) 2>&1 | tee -a "$LOG"

  log "==> docker compose up -d (DIST_PERF_TRACE=1 DIST_PERF_TRACE_GGML=1)"
  (cd "$DOCKER_DIR" && DIST_PERF_TRACE=1 DIST_PERF_TRACE_GGML=1 docker compose up -d --force-recreate) 2>&1 | tee -a "$LOG"

  log "==> smoke"
  (cd "$DOCKER_DIR" && ORCH_URL=http://127.0.0.1:9000 ./smoke.sh) 2>&1 | tee -a "$LOG"

  log "==> benchmark (task12_docker + --profile-runtime)"
  cd "$ROOT"
  ORCHESTRATOR=http://127.0.0.1:9000 \
  BENCHMARK_DOCKER=1 \
  BENCHMARK_SKIP_DOCKER_PERF_RECREATE=1 \
  DIST_PERF_TRACE=1 \
  DIST_PERF_TRACE_GGML=1 \
  python3 benchmarks/benchmark_runner.py \
    --profile task12_docker \
    --model tinyllama \
    --cluster-size 3 \
    --profile-runtime \
    --output-dir "$OUT_DIR/benchmark" 2>&1 | tee -a "$LOG"

  log "==> collect traces from containers"
  n_files="$(collect_traces "$OUT_DIR")"
  log "  collected $n_files jsonl files"
  if [[ "$n_files" -lt 1 ]]; then
    log "FAIL: no perf trace jsonl in containers"
    docker exec dist-node-a ls -laR /data/models/perf_trace 2>&1 | tee -a "$LOG" || true
    exit 1
  fi

  log "==> merge + postprocess"
  mkdir -p "$OUT_DIR/merged"
  PYTHONPATH="$ROOT/benchmarks" python3 "$ROOT/benchmarks/perf_trace/postprocess.py" \
    --raw "$OUT_DIR/raw" \
    --out "$OUT_DIR" \
    --profile task12_docker \
    --model tinyllama \
    --cluster-size 3 \
    --baseline-dir "$ROOT/logs/perf_trace/_baselines" \
    --pin-if-missing 2>&1 | tee -a "$LOG"

  log "==> validate"
  validate_analysis "$OUT_DIR/analysis" 2>&1 | tee -a "$LOG"
  validate_install "$OUT_DIR/install_analysis" 2>&1 | tee -a "$LOG"
  validate_session "$OUT_DIR/session_analysis" 2>&1 | tee -a "$LOG"
  validate_ttft "$OUT_DIR/ttft_analysis" 2>&1 | tee -a "$LOG"
  validate_gpu "$OUT_DIR/analysis" 2>&1 | tee -a "$LOG"
  validate_ggml "$OUT_DIR/analysis" 2>&1 | tee -a "$LOG"
  validate_budget "$OUT_DIR/analysis" 2>&1 | tee -a "$LOG"
  validate_regression "$OUT_DIR/analysis" 2>&1 | tee -a "$LOG"
  validate_timeline "$OUT_DIR/analysis" 2>&1 | tee -a "$LOG"

  log "Done. Artifacts: $OUT_DIR"
}

main "$@"
