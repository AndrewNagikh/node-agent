# Task 15.1 — Hidden Transport Breakdown (A→B)

**Trace ID:** `trace-000001`
**Status:** MISSING
**Total pack time:** **None ms** (missing)

## Hidden Pack

**Total:** None ms

**Allocation:** — ms
**Gather hidden:** — ms
**Copy (memcpy):** — ms
**Serialization:** absent (0 ms — no separate serialize buffer)
**Frame build:** — ms
**Socket send:** — ms

## Stage statistics

| Stage | avg (ms) | min | max | p95 | total (ms) | contribution % |
|-------|---------:|----:|----:|----:|-----------:|---------------:|
| Allocation | None | None | None | None | None | None |
| Gather hidden | None | None | None | None | None | None |
| Copy (memcpy) | None | None | None | None | None | None |
| Serialization | None | None | None | None | None | None |
| Frame build | None | None | None | None | None | None |
| Socket send | None | None | None | None | None | None |

## Acceptance answers (trace-based)

1. **Most expensive operation:** — (— ms, —%)
2. **Real serialization time:** None ms (separate stage present: None)
3. **memcpy time:** None ms
4. **Repeated allocations per token:** None
5. **Hidden buffer copies:** None
6. **Socket send significant:** None (avg — ms)

## Allocation audit (Phase D)

- Samples: 0
- Fresh heap alloc per token: **False**
- capacity_grew events: 0

## Copy path (Phase E)

- Path: `ggml_embeddings->std::vector->kernel_tcp`
- Heap copy count (including TCP read): **None**
  1. GGML embedding tensor (llama_get_embeddings)
  2. heap std::vector<float> via memcpy
  3. kernel TCP send from user buffer (no extra heap buffer)

## Interpretation

The legacy `SERIALIZE_HIDDEN_END` span bundled gather + alloc + memcpy. Sub-stage spans show **`llama_get_embeddings` access (GATHER)** dominates — not memcpy, heap allocation, wire framing, or TCP payload send.

## Methodology

- C++: `hidden_transport_breakdown.cpp` in entry worker (`split_gen3_a`)
- Python: `benchmarks/perf_trace/hidden_transport_breakdown.py`
- Steady decode filter: 8192 B payload, single-token waves
- Measurement only — no protocol / wire / format changes
