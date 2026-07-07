# v0.3.0 — Runtime performance profiler and metrics

Tag: `v0.3.0`

Date: 2026-07-07

## Summary

Release `v0.3.0` freezes Task 12: the distributed runtime performance profiler, benchmark integration, and pipeline stall analysis toolchain.

This release is **measurement-only**. It does not change planner, descriptor, scheduler, or inference hot-path semantics relative to `v0.2.1`. When `DIST_PERF_TRACE=0` (default), trace overhead is designed to be zero.

Key analysis artifacts:

- Profiler specification: `docs/TASK_12_RUNTIME_PERFORMANCE_PROFILER.md`
- Pipeline stall proof (Docker): `docs/TASK_12_PIPELINE_STALL_ANALYSIS_DOCKER.md`
- Single-token drill-down: `docs/TASK_12_TOKEN_17_TIMELINE.md`
- Homelab E2E benchmark report: `docs/LAN_HOMELAB_BENCHMARK_REPORT_20260706.md`

## Release Highlights

- C++ perf trace hooks in orchestrator, node_agent, and `split_gen3_*` workers (`DIST_PERF_TRACE=1`).
- JSONL event export with phase separation: `ttft`, `decode`, `session_create`, `install`.
- Python analysis pipeline: `benchmarks/perf_trace/` (merge, bottleneck, TTFT, utilization, stall analysis, HTML timeline).
- Benchmark profiles: `task12_docker`, `homelab_full`, `runtime_profile` with `perf_trace: true`.
- Docker verification script: `scripts/verify_docker_perf_trace.sh`.
- Homelab trace runners: `scripts/run_homelab_benchmark.sh`, `scripts/run_homelab_perf_trace.sh`.

## Task 12 Findings (proven, not assumed)

Steady-state decode on 3-node Docker CPU (TinyLlama):

| Metric | Value |
|--------|------:|
| Wall period per token | 54.4 ms |
| Critical path (entry recv to final compute end) | 13.6 ms |
| Pipeline bubble (idle) | 40.8 ms (75%) |
| Network per hop | < 0.1 ms |
| HTTP benchmark throughput | ~15.4 tok/s |

**Conclusion:** the bottleneck is synchronous request-response protocol architecture, not network or model compute. This release provides the measurement foundation for Runtime Protocol v2 (RFC-0013).

## Architecture Frozen in This Release

- v0.2.1 layer-first runtime architecture unchanged.
- Passive instrumentation only; no pipelining, batching, or protocol changes.
- Trace correlation uses `trace_id` + per-worker `token_idx` (v1 model); WaveID is deferred to RFC-0013 / Task 13.
- Benchmark separates TTFT from decode; `tokens.csv` sum-of-stages is documented as invalid for throughput.

## Verification

```bash
cmake --build llama.cpp/build --target split_gen3_a split_gen3_b split_gen3_c node_agent orchestrator test-perf-trace -j8
llama.cpp/build/bin/test-perf-trace

# Docker perf trace verify (3-node cluster)
./scripts/verify_docker_perf_trace.sh

# Task 12 benchmark profile
DIST_PERF_TRACE=1 BENCHMARK_DOCKER=1 \
  python3 benchmarks/benchmark_runner.py \
  --profile task12_docker \
  --profile-runtime \
  --output-dir logs/perf_trace/task12_release_verify

# Python analysis tests
python3 -m pytest benchmarks/perf_trace/ -q
```

## Known Follow-ups

- Postprocess alignment still requires ordinal fallback when `token_idx` diverges across workers (addressed in RFC-0013 via WaveID).
- Homelab `qwen14b` / large-model session create remains a separate track from Task 12 decode analysis.
- RFC-0013 (Runtime Protocol v2) is design-only until accepted; no runtime protocol changes in this release.

## Relationship to v0.2.1

This is a metrics and observability release. Runtime correctness, descriptor model, and layer-first inference path remain those documented in:

- `docs/RELEASE_v0.2.md`
- `docs/RELEASE_v0.2.1.md`

## GitHub Release

```bash
gh release create v0.3.0 \
  --title "v0.3.0 — Runtime performance profiler and metrics" \
  --notes-file docs/RELEASE_v0.3.0.md
```
