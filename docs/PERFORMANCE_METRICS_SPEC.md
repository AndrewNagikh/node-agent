# Performance Metrics Specification

**Task:** 14 — Complete Runtime Observability  
**Scope:** Benchmark analysis and runtime instrumentation for decode-phase spans.  
**Principle:** Every aggregated number must cite source events, formula, trace_id, and validity status. UNKNOWN metrics are excluded from Performance Summary.

---

## Status Vocabulary

| Status | Meaning |
|--------|---------|
| **PASS** | Metric computed from complete, consistent inputs |
| **FAIL** | Required instrumentation missing or gate threshold breached |
| **UNKNOWN** | Cannot compute — incomplete spans (report reason, do not guess) |
| **INVALID** | Computed values contradict each other (e.g. TPS > ceiling) |
| **SKIP** | Metric not applicable for this run profile |

When middle/final **decode** spans are absent, bubble, ceiling, utilization, and critical path MUST be **UNKNOWN**, not FAIL against RFC thresholds.

---

## Required Decode Trace Chain (per token / WaveID)

For homelab validation, each steady-state decode token MUST produce:

```
ENTRY_RECEIVE
  → ENTRY_COMPUTE_BEGIN → ENTRY_COMPUTE_END
  → ENTRY_SEND_END (or HIDDEN_TRANSFER)
MIDDLE_RECEIVE
  → MIDDLE_COMPUTE_BEGIN → MIDDLE_COMPUTE_END
  → MIDDLE_SEND_END (or HIDDEN_TRANSFER)
FINAL_RECEIVE
  → FINAL_COMPUTE_BEGIN → FINAL_COMPUTE_END
  → SAMPLER_BEGIN → SAMPLER_END
CLIENT_RESPONSE / GENERATE_END (orchestrator)
```

All events above MUST have `phase=decode`. Events with correct names but `phase=session_create` are **mislabeled** and do not satisfy instrumentation (Task 13.1 FAIL).

Implementation: `benchmarks/perf_trace/metric_validation.py` → `STAGE_DECODE_CHAIN`, `build_token_chain()`.

---

## Metrics

### 1. TPS (Throughput)

| Field | Value |
|-------|-------|
| **Meaning** | Steady-state decode tokens per second |
| **Source of truth** | Orchestrator `/session/generate` response `timing` |
| **Formula** | `TPS = generated_tokens / decode_ms × 1000` |
| **Required inputs** | `timing.decode_ms`, `timing.generated_tokens` (or `token_count`) |
| **Required trace** | Optional; used for cross-check only |
| **Invalid when** | `decode_ms ≤ 0` or `generated_tokens ≤ 0` |
| **Cross-check** | If `TPS > ceiling_tps × 1.05` → **METRIC INVALID** |

All report TPS figures MUST derive from this field or explicitly cite trace-derived TPS with its own validity block.

---

### 2. Critical Path (ms/token)

| Field | Value |
|-------|-------|
| **Meaning** | Minimum serial time for one token through the 3-stage pipeline |
| **Formula (wall)** | `final_recv_us + final_compute_us − entry_recv_us` (per WaveID) |
| **Formula (serial)** | `entry_compute + transfer_ab + middle_compute + transfer_bc + final_compute + sampling` |
| **Formula (compute sum)** | `entry_compute_ms + middle_compute_ms + final_compute_ms` (partial fallback) |
| **Effective path** | Wall when clocks align; serial span sum when cross-node `steady_clock` skew detected (`wall > 5× serial` or `wall > 120s`) |
| **Events** | `ENTRY_RECEIVE`, `FINAL_RECEIVE`, `*_COMPUTE_END`, `HIDDEN_TRANSFER`, `SAMPLER_END` per stage |
| **Required trace** | Full decode chain on entry, middle, final for same `trace_id` + `WaveID` |
| **UNKNOWN when** | Any stage missing decode-phase spans for the trace |

Report: `avg_effective_critical_path_ms`, `clock_skew_detected` in `validation.json`. `avg_wall_critical_path_ms` excludes skewed waves.

---

### 3. Ceiling TPS

| Field | Value |
|-------|-------|
| **Meaning** | Theoretical max TPS if bubble = 0 |
| **Formula** | `ceiling_tps = 1000 / avg_effective_critical_path_ms` |
| **Fallback order** | wall clock → serial span sum (clock skew) → compute-only sum |
| **Required trace** | Same as critical path |
| **UNKNOWN when** | Critical path not computable |

Ceiling MUST NOT be hand-estimated in reports.

---

### 4. Bubble

| Field | Value |
|-------|-------|
| **Meaning** | Protocol / scheduling idle as % of inter-token period |
| **Formula** | `bubble_ms = entry_period_ms − effective_critical_path_ms` |
| | `entry_period_ms = entry_recv[wave N] − entry_recv[wave N−1]` |
| | `bubble_pct = bubble_ms / entry_period_ms × 100` |
| **Events** | `ENTRY_RECEIVE` (consecutive waves), full critical path spans |
| **Required trace** | Decode spans on **all three stages** |
| **UNKNOWN when** | Middle or final decode spans missing — **do not use tokens.csv** |

RFC §29 gate: bubble < 10% only evaluated when status = PASS.

---

### 5. Pipeline Utilization

| Field | Value |
|-------|-------|
| **Meaning** | Fraction of wall period spent in COMPUTE |
| **Formula (current)** | `100 × avg(compute_ms) / avg(total_ms)` from token rows |
| **Events** | Per-stage `*_COMPUTE_END` spans with `category=COMPUTE`, `phase=decode` |
| **UNKNOWN when** | Middle/final compute spans absent in decode phase |

Task 12 target: ≥ 90%. Task 13.1 requires validity before gate evaluation.

---

### 6. Worker Idle

| Field | Value |
|-------|-------|
| **Meaning** | WAIT + unaccounted idle as % of token wall time |
| **Formula** | `100 × (wait_ms + idle_gap) / total_ms` per token, averaged |
| **Events** | `category=WAIT|IDLE`, `SCHED_QUEUE_WAIT` (GGML) |
| **UNKNOWN when** | Incomplete per-stage wait spans |

---

### 7. TTFT

| Field | Value |
|-------|-------|
| **Meaning** | Time to first token (client wall) |
| **Source** | `timing.ttft_ms` from generate response OR `ttft.json` from trace |
| **Formula** | Client-reported prefill completion |
| **Required trace** | `CLIENT_TTFT` / `TTFT_PREFILL` events (optional) |

---

### 8. Hidden Transfer / Serialization

| Field | Value |
|-------|-------|
| **Meaning** | Per-hop network + serialize latency |
| **Formula** | `avg(HIDDEN_TRANSFER.dur_us)`; serialize from attrs or SERIALIZATION category |
| **Events** | `HIDDEN_TRANSFER`, `ENTRY_SEND_END`, `MIDDLE_SEND_END` |
| **Required trace** | Decode-phase network events on entry (and middle for BC hop) |

---

### 9. Queue Depth

| Field | Value |
|-------|-------|
| **Meaning** | Inbound wave queue occupancy during decode |
| **Formula** | `max(QUEUE_DEPTH.attrs.depth)` per stage |
| **Events** | `QUEUE_DEPTH`, `WAVE_QUEUED` |
| **Required trace** | Decode phase on entry, middle, final |
| **Gate** | max ≥ 2 for RFC-0013 overlap verification |

---

## Validation Output

Post-process writes:

| Artifact | Path | Content |
|----------|------|---------|
| Unified timeline | `perf_trace/analysis/timeline.json` | All decode events sorted by WaveID, stage, ts_us |
| Critical path | `perf_trace/analysis/critical_path.json` | Per-token serial + wall critical path |
| Bubble | `perf_trace/analysis/bubble.json` | Entry period minus critical path |
| Utilization | `perf_trace/analysis/utilization.json` | Busy/idle per stage |
| Serialization | `perf_trace/analysis/serialization.json` | Serialize/deserialize hidden spans |
| Network | `perf_trace/analysis/network.json` | Per-hop latency, payload, throughput |
| Scheduler | `perf_trace/analysis/scheduler.json` | Queue/RPC/network/idle wait breakdown |
| Validation | `perf_trace/analysis/validation.json` | Automated invariant checks |
| Validation (human) | `perf_trace/analysis/validation.md` | Summary for reports |

Benchmark report includes **Task 14 Runtime Observability** when `--profile-runtime` is used. Metrics with status **UNKNOWN** are omitted from the Performance Summary.

Example checks:

```
PASS  decode_trace_entry
FAIL  decode_trace_middle   reason: missing decode spans; found mislabeled in session_create
UNKNOWN bubble              reason: missing decode spans on one or more stages
INVALID tps_vs_ceiling      reason: measured TPS > theoretical ceiling
```

---

## Task 14 Exit Criteria

Task 14 is complete when a steady-state homelab run produces:

- `decode_trace_entry|middle|final` = **PASS** (all events `phase=decode`)
- Full per-token chain in `timeline.json` without gaps
- `critical_path.json`, `bubble.json`, `utilization.json` all **PASS** (not UNKNOWN)
- `validation.json` overall = **PASS** and `tps_vs_ceiling` = **PASS**
- No UNKNOWN metric appears in the benchmark Performance Summary

Task 15 (Runtime Performance Optimization) begins only after the above.

---

## References

- RFC-0013 §19 — bubble / period / critical path definitions
- RFC-0013 §29 — acceptance criteria gates
- `benchmarks/perf_trace/metric_validation.py` — implementation
- `docs/archive/TASK_13_1_PERFORMANCE_INSTRUMENTATION_VALIDATION.md` — task checklist
