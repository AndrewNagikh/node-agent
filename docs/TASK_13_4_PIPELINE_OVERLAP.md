# Task 13.4 — Pipeline Overlap (RFC-0013 Phase 4)

**Status:** Complete  
**RFC:** [`RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md`](RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md)  
**Depends on:** Task 13.3 (entry queue)  
**Phase:** Migration §25 Phase 4 — Pipeline overlap

---

## Goal

Middle and final stages accept the next wave while the prior wave is still in flight. Entry pipelines AB hidden sends without blocking on mid/final responses.

**Exit criteria:** Steady decode shows `middle_queue_depth > 1` and `final_queue_depth > 1` in `queue.json`; pipeline bubble < 50% vs v1 baseline (intermediate gate).

---

## Enable (opt-in)

```bash
export DIST_RUNTIME_PROTOCOL_V2=1
export DIST_RUNTIME_ENTRY_QUEUE=1          # required for orchestrator pipelining
export DIST_RUNTIME_STAGE_QUEUE=1          # middle + final + entry AB pipelining
export DIST_RUNTIME_STAGE_QUEUE_DEPTH=2    # optional, default = entry depth (2)
```

Without `DIST_RUNTIME_STAGE_QUEUE`, middle/final remain synchronous single-thread loops (v1 behavior).

---

## Architecture

| Stage | Receiver thread | Processor | Response thread |
|-------|-----------------|-----------|-----------------|
| Entry | ctrl → inbound queue | compute + AB send | AB → TOKEN_READY + complete |
| Middle | AB → inbound queue | compute + BC send | BC → mid_resp on AB |
| Final | BC → inbound queue | compute + sample | (inline c_resp) |

Trace events: `WAVE_QUEUED`, `QUEUE_DEPTH` per stage (`entry`, `middle`, `final`).

---

## Components

| File | Change |
|------|--------|
| `workers/wave_inbound_queue.h/cpp` | `hidden_inbound_queue`, `runtime_stage_queue_*` |
| `workers/split_gen3_a.cpp` | AB response thread when stage queue on |
| `workers/split_gen3_b.cpp` | AB receiver + BC responder threads |
| `workers/split_gen3_c.cpp` | BC receiver + processor threads |
| `transport/runtime_entry_queue.h/cpp` | `runtime_stage_queue_client_enabled()` |

---

## Next phase

**Task 13.5 — Full async:** orchestrator non-blocking dispatch; bubble < 10%; TPS ≥ 0.8 × ceiling.
