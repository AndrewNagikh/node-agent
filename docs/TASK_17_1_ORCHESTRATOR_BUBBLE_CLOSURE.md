# Task 17.1 — Orchestrator Bubble Closure (Homelab)

**Type:** Investigation → Implementation (two gated phases)
**Status:** Phase B landed on Docker — **root cause was TCP Nagle/delayed-ACK**; homelab validation pending

> **Phase B result (2026-07-13): the Docker bubble was the TCP delayed-ACK timer, not scheduler logic.**
> The Phase A attribution signature (two ~41 ms waits per token; v1 bubble measured at exactly 40.8 ms in Task 12) matched the classic Nagle + delayed-ACK interaction for small-message request/response ping-pong. The transport never set `TCP_NODELAY`. Setting it on every connected/accepted socket (`split_tcp_wire.cpp`, opt-out `DIST_TCP_NODELAY=0`):
>
> | Metric (Docker CPU, llama3-1b, 16 tok) | Before | After |
> |---|---|---|
> | Decode TPS | 11.4 | **60.7** (x5.3) |
> | Period | 82.1 ms | 16.3 ms |
> | `ack_wait` / `complete_wait` | 41.0 / 41.0 ms | 0.05 / 0.007 ms |
> | `token_wait` (pipeline work) | 0.01 ms | 15.8 ms |
> | **Bubble** | 71.2% | **2.5% — RFC-0013 §28 gate (<10%) PASS for the first time** |
>
> Determinism: identical token sequences across repeated runs (PASS). Attribution after fix: 99.4% of period. The v2 client pipeline now behaves as designed — the client is genuinely waiting on pipeline compute (`token_wait`), not on protocol stalls.
>
> **Homelab implication:** the homelab 11.7 ms bubble (Task 16) likely contains the same component in macOS/Windows delayed-ACK form; the homelab validation run must re-measure bubble and TPS with this fix before any further Phase B scheduler work is considered.
**Parent:** Research 17 — `docs/DISTRIBUTED_INFERENCE_PERFORMANCE_STUDY.md` (roadmap R1)
**Depends on:** RFC-0013 Phases 3–6 (built, default-on since Task 13.6)
**Expected gain:** 25.8 → ~34–37 tok/s (**+43% max**) — the single largest measured lever

> **Phase A instrumentation landed (2026-07-12):**
> - `RUNTIME_FLAGS` instant (stage `client`) + `protocol_version` / `entry_queue` / `stage_queue` / `client_pipeline` / `external_embedding` fields in generate `timing` → benchmark `results.json`.
> - Client decode-loop spans in `node_agent.cpp`: `CLIENT_TOKEN_WAIT`, `CLIENT_ACK_WAIT`, `CLIENT_COMPLETE_WAIT`, `CLIENT_SEND`, `CLIENT_EMBED` (queued path) and `CLIENT_BLOCKING_RT` (v1 path).
> - Analyzer: `benchmarks/perf_trace/client_loop_breakdown.py` → `analysis/client_loop_breakdown.{json,md}` with per-stage attribution, unattributed gap, and the ≥90% gate.
>
> ```bash
> PYTHONPATH=benchmarks python3 benchmarks/perf_trace/client_loop_breakdown.py \
>   logs/perf_trace/<run>/raw --trace <trace-id> --docs docs/TASK_17_1A_CLIENT_LOOP_BREAKDOWN.md
> ```
> Homelab attribution run pending (Phase A exit); Phase B scope follows from it.
>
> **Confirmed Docker attribution (llama3-1b, 16 tok, v2 client pipeline ON, 2026-07-13, re-verified across two independent runs):** period **82.1 ms**; `ack_wait` **~41.0 ms (50%)** + `complete_wait` **~41.0 ms (50%)**; `token_wait`/`send` ~0 ms; attribution **95.9%** (gate PASS). **Finding:** the client pipeline is not pipelined in practice — after dispatching wave N+1 the client blocks on the queue ack, and then on COMPLETE of wave N; the entry appears to grant the ack / send COMPLETE only after processing, so waves never overlap; `ack_wait` and `complete_wait` alone account for essentially the entire period. Phase B target: entry must ack enqueue immediately and COMPLETE must not gate the next wave.
>
> Infrastructure/tooling issues found and fixed en route (none in the decode/runtime path itself):
> - `node_agent` perf config reload — long-lived process cached `DIST_PERF_TRACE=0` at first read, so the generate handler's enable never took effect until process restart with the env var set.
> - `collect_traces` basename collision — `decode/` vs `ttft/` files share basenames and silently overwrote each other in collected artifacts; fixed by preserving the subdir in the destination filename (`scripts/verify_docker_protocol_v2.sh`).
> - **`client_loop_breakdown.py` shared-volume duplication** — all 4 Docker containers mount the same `dist-perf-trace` volume; naively collecting `*.jsonl` from every container copies identical files N times (a pre-existing property of this collection pattern, previously masked because the official analyzers already dedup — see `docs/archive/TASK_12_PIPELINE_STALL_ANALYSIS_DOCKER.md` methodology). Added the same `(event, node_id, WaveID|token_idx, ts_us, kind)` dedup key used by `pipeline_stall_analysis.load_deduped()`.
> - **`client_loop_breakdown.py` cross-call bucket collision** — a benchmark scenario issues two `/pipeline/generate` calls (warmup, then measured) under the *same* `trace_id`, and each resets its own step counter to 0, so naive `token_idx`-keyed wave buckets silently summed warmup and measured spans together (inflating attribution to 190–770% of period). Fixed by segmenting the event stream on `RUNTIME_FLAGS` instants (emitted once per `/pipeline/generate` call) and analyzing only the last segment. Both fixes are covered by regression tests (`test_client_loop_breakdown.py`).
>
> **Docker verification summary (2026-07-13):** full `verify_docker_protocol_v2.sh` gate suite re-run against Phase 1 (17.1A + 17.2A + 17.3 code, sync-split flags default off): `validation_overall=PASS`, queue depth max=2 on entry/middle/final (overlap confirmed), bubble 71.2% < 75% regression threshold (matches Task 13.5 Docker CPU baseline, embedding-bound — not a regression), 16-token v2 generate completed end-to-end exercising all new instrumentation. **v1 rollback:** one clean run completed session_create/warmup/generate all HTTP 200 confirming the v1 (`CLIENT_BLOCKING_RT_*`) code path is sound; subsequent reinstall attempts hit a **pre-existing, protocol-independent** planner/coverage race (`session_create` intermittently returns a stale 2-node layout with `"node-X prepare: missing layer tensor"` after a model reset+reinstall cycle — reproduced under both v1 and v2 flags, and observed once before touching any Phase 1 code). Not caused by this diff (no planner/coverage/registration files touched); flagged as a candidate addition to Task 18.1's runtime lifecycle stability scope (parallel to the existing qwen14b finding). One node (`dist-node-a`) segfaulted (exit 139) once, correlated with an ad-hoc 20x rapid-retry `session/create` loop used for manual debugging (not part of the verify script's normal 5x/10s-interval cadence) — line-by-line diff review of `node_agent.cpp` found no lifetime/pointer issue that could explain it, and it did not reproduce across 3 subsequent clean reinstall cycles.

---

## Problem

Protocol v2 (entry queue, stage queues, client pipelining) is default-on since Task 13.6, yet the homelab Task 14/16 trace (`trace-000010`) still shows an **11.73 ms inter-token bubble (30% of period)**. Docker Task 13.5 measured bubble ~72% *with pipelining active*. Conclusion of Research 17 §6.1: pipelining may have moved the blocking point rather than removed it — or was not engaged at all on homelab. RFC-0013 §28 gate (<10%) has never been evaluated as PASS on homelab.

## Phase A — Verify & attribute (no runtime changes)

1. Re-run homelab TinyLlama generate with **explicitly logged** runtime flags (`DIST_RUNTIME_PROTOCOL_V2`, `DIST_RUNTIME_ENTRY_QUEUE`, `DIST_RUNTIME_STAGE_QUEUE`, `DIST_RUNTIME_CLIENT_PIPELINE`) captured into the trace/bench artifacts. Flag state must be part of `results.json`, not assumed.
2. Confirm queue depths ≥ 2 on all stages during steady decode (`queue.json`); confirm wave N+1 dispatch occurs after `TOKEN_READY` of wave N, before `COMPLETE` (Task 13.5 protocol).
3. Attribute the remaining inter-token gap with orchestrator-side spans: HTTP handling, token commit, response bookkeeping, `GENQ`/`GENT`/`DRAIN` waits, queue push/pop. Every ms of the bubble must land in a named span (Task 15.1b methodology).

**Phase A exit:** a breakdown table where `Σ(attributed spans) ≥ 90% × bubble_ms`, plus a verdict: *pipelining inactive* vs *pipelining active but serialized on X*.

## Phase B — Close the gap

Scope depends on Phase A verdict; candidate fixes (in expected order): flag plumbing on homelab deploy scripts, orchestrator dispatch thread decoupled from response unwind, per-wave bookkeeping moved off the dispatch path. **No GGML or worker compute changes in this task.**

## Acceptance criteria

| Gate | Threshold | Source |
|------|-----------|--------|
| Runtime flags recorded in artifacts | required | `results.json` / trace attrs |
| Queue depth steady decode | max ≥ 2 all stages | `queue.json` |
| **Bubble** | **< 10% of period** (RFC-0013 §28) | `bubble.json`, status PASS per `PERFORMANCE_METRICS_SPEC.md` |
| TPS (homelab TinyLlama, 32 tok) | ≥ 0.9 × ceiling_tps = ≥ ~33 tok/s | `validation.json`, `tps_vs_ceiling` PASS |
| Determinism | same seed → same tokens vs v2 baseline | generate parity check |
| No UNKNOWN metrics in summary | required | Task 14 rules |

## Non-goals

Sampling path (17.2), GPU sync (17.3), compute inflation (17.4), any wire-format change (retired by Research 17 F3).

## References

`docs/DISTRIBUTED_INFERENCE_PERFORMANCE_STUDY.md` §6.1 §12; `docs/archive/TASK_12_PIPELINE_STALL_ANALYSIS_DOCKER.md`; `docs/archive/TASK_13_5_FULL_ASYNC.md`; `docs/RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md` §19–20, §28.
