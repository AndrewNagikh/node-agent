# Task 13.1 — WaveID Tracing (RFC-0013 Phase 1)

**Status:** Complete  
**RFC:** [`RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md`](RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md) (Accepted)  
**Phase:** Migration §25 Phase 1 — Tracing  
**Protocol change:** None (v1 RPC unchanged)

---

## Goal

Add `WaveID` to all decode-phase perf_trace events without changing the wire protocol.

**Exit criteria:** All decode events carry `WaveID` in trace JSONL; v1 protocol behavior unchanged.

---

## v1 WaveID Mapping

| Work unit | WaveID | token_idx (deprecated alias) |
|-----------|-------:|------------------------------|
| Prefill | 0 | -1 |
| Decode step `step` (1-based in orchestrator loop) | `step` | `step - 1` |

Orchestrator (`node_agent`) is authoritative via `active_context.json`. Workers derive the same WaveID from local `debug_step`:

- prefill: `WaveID = 0`
- decode: `WaveID = debug_step` (1 for first decode after prefill)

---

## Schema

Every decode/generate perf event now includes:

```json
{
  "WaveID": 3,
  "token_idx": 2,
  "trace_id": "trace-000002",
  "phase": "decode",
  "event": "ENTRY_COMPUTE_END"
}
```

Install, session, and TTFT events use `WaveID: -1` (not applicable).

---

## Files

| Area | Files |
|------|-------|
| Trace core | `runtime_debug/perf_trace.h`, `perf_trace.cpp`, `perf_ggml.cpp` |
| Orchestrator path | `node_agent.cpp` (auto-derive via `perf_trace_set_context`) |
| Workers | `split_gen3_a/b/c.cpp` |
| Analysis | `benchmarks/perf_trace/merge.py`, `queue.py`, `pipeline_stall_analysis.py` |

---

## Next Phase

**Task 13.2 — Wire envelope:** v2 event framing alongside v1 RPC (RFC §25 Phase 2).
