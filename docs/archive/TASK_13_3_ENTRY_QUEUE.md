# Task 13.3 — Entry Queue (RFC-0013 Phase 3)

**Status:** Complete  
**RFC:** [`RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md`](RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md)  
**Depends on:** Task 13.2 (wire envelope + negotiation)  
**Phase:** Migration §25 Phase 3 — Entry queue

---

## Goal

Entry stage accepts asynchronous wave enqueue with configurable depth. Pipelined dispatch uses early `TOKEN_READY` so queue depth can exceed 1 during decode.

**Exit criteria:** `queue.json` shows `entry_queue_depth > 1` during steady decode when v2 + entry queue enabled.

---

## Enable (opt-in)

```bash
export DIST_RUNTIME_PROTOCOL_V2=1          # client + entry worker
export DIST_RUNTIME_ENTRY_QUEUE=1          # entry worker + node_agent
export DIST_RUNTIME_ENTRY_QUEUE_DEPTH=2  # optional, default 2
```

Without these flags, behavior remains v1 synchronous RPC.

---

## Protocol extensions (ctrl channel)

| Message | Magic | Purpose |
|---------|-------|---------|
| `split_gen_queue_ack` | `GENQ` | Immediate ack after enqueue; carries `queue_depth`, `wave_id` |
| `split_gen_token_ready` | `GENT` | Early sampled token before full `split_gen3_a_resp` |

Client flow per decode step (pipelined):

1. `send DECODE`
2. `recv GENQ` (queue ack)
3. `recv GENT` (token ready)
4. `send DECODE` next step (before complete) — achieves depth > 1
5. `recv GENQ` for pipelined step
6. `recv split_gen3_a_resp` (complete)

---

## Components

| File | Role |
|------|------|
| `workers/wave_inbound_queue.h/cpp` | Thread-safe FIFO with backpressure |
| `workers/split_gen3_a.cpp` | Receiver thread + processor; `WAVE_QUEUED` trace |
| `transport/runtime_entry_queue.h/cpp` | Client-side queued/pipelined helpers |
| `node_agent.cpp` | `pipeline_gen3_decode_pipelined` decode loop |

---

## Trace events

- `WAVE_QUEUED` — wave accepted into entry inbound queue
- `QUEUE_DEPTH` — observable depth including in-flight processor slot

---

## Next phase

**Task 13.4 — Pipeline overlap:** middle/final inbound queues (RFC §25 Phase 4, bubble < 50% gate).
