# Task 18.1 — Fast Session Lifecycle (Warm Start ≤ 2 s)

**Type:** Implementation (lifecycle/control plane; decode path untouched)
**Status:** Planned
**Parent:** Research 17 (roadmap R6); startup track — independent of Task 17.x decode track
**Expected gain:** session create 10.3–61.5 s → ≤ 2 s warm (TinyLlama), ≤ 5 s (8B); unblocks ≥14B validation

---

## Problem

Research 17 §7: nothing in the warm startup path is fundamental. Measured session create is dominated by per-session worker spawn + per-node model load + READY polling. Additionally `qwen3-14b` fails `session_create` with HTTP 500 (~94 s, orchestrator error string not persisted) — blocking exactly the model region where the distributed architecture wins outright (Study §10).

## Scope (ordered)

### 18.1a — qwen14b unblock + diagnosability

1. Persist orchestrator `error` body into benchmark `StageRecord` for failed stages (LAN report §"Missing diagnostic").
2. Reproduce isolated `POST /session/create` for qwen3-14b; fix root cause in `setup_runtime_graph()` path.
3. Re-run homelab benchmark from `qwen14b` → `qwen30b` → `gemma27b`.

### 18.1b — Persistent workers

Workers survive session teardown: model weights, contexts, and pipeline listeners stay resident; a new session binds to existing workers when (model, layer ranges, topology) match. Session create becomes control-plane registration + KV reset.

### 18.1c — Event-driven readiness

Replace READY polling with worker → orchestrator state-change callbacks (state machine from Task 11 unchanged: STARTING → MODEL_LOADING → LISTENER_READY → PIPE_READY → READY).

### 18.1d — Graph reserve cache

Graph reserve computed once per (model, n_ctx shape) per worker process lifetime; subsequent sessions reuse.

*(TensorProvider — Task 11.7 — remains its own workstream; 18.1 must not depend on it.)*

## Acceptance criteria

| Gate | Threshold | Source |
|------|-----------|--------|
| qwen14b session_create | HTTP 200, generate 32 tokens | benchmark re-run |
| Error persistence | failed stages carry orchestrator error body | trace inspection |
| Warm session create (workers resident, same model) | ≤ 2 s TinyLlama, ≤ 5 s qwen8b | benchmark timing |
| Second session on same model | ≤ 1 s | targeted test |
| Cold path unchanged semantics | full matrix still passes (8/8 + 14B) | infra benchmark |
| Decode non-regression | TPS within ±3% of pre-task baseline | `validation.json` |
| Memory | orchestrator RSS < 300 MB invariant preserved (Task 10) | `/debug/rss` |

## Risks

Worker state leakage between sessions (KV, sampler state) — needs explicit reset contract; multi-model residency vs node memory budgets — keep single-resident-model policy first.

## Non-goals

Decode-path performance (Task 17.x); TensorProvider (11.7); sync/download throughput optimization (separate, ~50× headroom noted in Study §7.2 — candidate Task 18.2).

## References

Study §7–8; `docs/archive/LAN_HOMELAB_BENCHMARK_REPORT_20260706.md`; `docs/archive/TASK_11_FULL_METRICS_AND_ARCHITECTURE_REPORT_20260706.md`; `docs/ORCHESTRATOR_RUNTIME_BOUNDARY.md`.
