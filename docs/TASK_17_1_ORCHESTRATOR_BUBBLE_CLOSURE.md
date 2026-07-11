# Task 17.1 — Orchestrator Bubble Closure (Homelab)

**Type:** Investigation → Implementation (two gated phases)
**Status:** Planned
**Parent:** Research 17 — `docs/DISTRIBUTED_INFERENCE_PERFORMANCE_STUDY.md` (roadmap R1)
**Depends on:** RFC-0013 Phases 3–6 (built, default-on since Task 13.6)
**Expected gain:** 25.8 → ~34–37 tok/s (**+43% max**) — the single largest measured lever

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
