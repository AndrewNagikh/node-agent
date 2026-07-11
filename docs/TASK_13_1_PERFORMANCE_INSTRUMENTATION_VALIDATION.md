# Task 13.1 — Performance Instrumentation Validation

**Status:** In progress  
**Depends on:** Task 13 (RFC-0013 migration code)  
**Blocks:** Task 14 (Performance Engineering)  
**Spec:** [`PERFORMANCE_METRICS_SPEC.md`](PERFORMANCE_METRICS_SPEC.md)

---

## Goal

Make every benchmark metric **reproducible and explainable** before any runtime optimization. No performance engineering until the measurer is trusted.

---

## Problem Statement (homelab 2026-07-11)

Observed contradictions:

| Claim | Issue |
|-------|-------|
| Critical path ≈ 29 ms | Manual / partial (entry-only spans) |
| Ceiling ≈ 34 tok/s | Derived from incomplete critical path |
| Measured ≈ 40 tok/s | Exceeds ceiling → **impossible** if definitions consistent |
| Bubble ≈ 85% | Computed from `tokens.csv` with **no middle/final decode spans** |

Middle/final workers emit pipeline events with `phase=session_create`, not `phase=decode`. Bubble is **not proven**.

---

## Acceptance Criteria

- [ ] **AC1** — Per-token chain reconstructable (entry → middle → final → client) for homelab trace
- [ ] **AC2** — Decode trace PASS on entry, middle, final (`phase=decode`)
- [ ] **AC3** — Each aggregated metric cites events + formula + trace_id in validation output
- [ ] **AC4** — TPS single source: orchestrator `decode_ms` / `generated_tokens`
- [ ] **AC5** — Critical path computed automatically (not manual)
- [ ] **AC6** — Ceiling from formula, not estimates
- [ ] **AC7** — Bubble = UNKNOWN when spans missing (never silent FAIL from partial data)
- [ ] **AC8** — TPS > ceiling → METRIC INVALID, report blocked for that metric

---

## Deliverables

| Item | Path |
|------|------|
| Metrics spec | `docs/PERFORMANCE_METRICS_SPEC.md` |
| Validation module | `benchmarks/perf_trace/metric_validation.py` |
| Post-process integration | `benchmarks/perf_trace/postprocess.py` |
| Benchmark report section | `benchmarks/benchmark_report.py` |
| Unit tests | `benchmarks/perf_trace/test_metric_validation.py` |

---

## Usage

After homelab run with trace:

```bash
ORCHESTRATOR=http://192.168.50.154:9000 \
BENCHMARK_DOCKER=0 \
DIST_PERF_TRACE=1 \
python3 benchmarks/benchmark_runner.py \
  --profile rfc0013_docker \
  --model llama3_1b \
  --cluster-size 3 \
  --profile-runtime \
  --output-dir logs/perf_trace/my_run
```

Check:

```bash
cat logs/perf_trace/my_run/perf_trace/analysis/validation.md
```

Standalone on existing raw trace:

```bash
PYTHONPATH=benchmarks python3 -m perf_trace.metric_validation \
  --raw logs/perf_trace/my_run/perf_trace/raw \
  --analysis logs/perf_trace/my_run/perf_trace/analysis \
  --results logs/perf_trace/my_run/results.json
```

(Add CLI if needed — currently via postprocess)

---

## Known Instrumentation Gaps (runtime — Task 14+)

1. Middle/final workers tag spans as `session_create` instead of `decode`
2. `tokens.csv` / `queue.json` aggregate entry-only when middle/final decode missing
3. `pipeline_stall_analysis.py` hardcodes node_id → stage mapping (homelab: middle=node-c, final=node-b)

Task 13.1 **detects** these; Task 14 **fixes** runtime instrumentation.

---

## Exit Criteria for Task 14

Homelab steady-state run (warm workers, no reset):

```
validation.json → overall: PASS
  decode_trace_entry:  PASS
  decode_trace_middle: PASS
  decode_trace_final:  PASS
  bubble:              PASS (computed, not UNKNOWN)
  tps_vs_ceiling:      PASS (not INVALID)
```
