# Task 15.1 — Hidden Transport Breakdown (A→B)

**Trace ID:** `trace-1784213813-0006`
**Status:** PASS
**Total pack time:** **3.629 ms** (HIDDEN_PACK_TOTAL_END)

## Hidden Pack

**Total:** 3.629 ms

**Allocation:** 0.005 ms
**Gather hidden:** 2.956 ms
**Copy (memcpy):** 0.003 ms
**Serialization:** 0.0 ms
**Frame build:** 0.421 ms
**Socket send:** 0.237 ms

## Stage statistics

| Stage | avg (ms) | min | max | p95 | total (ms) | contribution % |
|-------|---------:|----:|----:|----:|-----------:|---------------:|
| Allocation | 0.005 | 0.0 | 0.041 | 0.011 | 0.146 | 0.14 |
| Gather hidden | 2.956 | 1.078 | 6.009 | 5.328 | 91.625 | 81.45 |
| Copy (memcpy) | 0.003 | 0.001 | 0.021 | 0.009 | 0.106 | 0.08 |
| Serialization | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Frame build | 0.421 | 0.119 | 4.556 | 0.678 | 13.467 | 11.6 |
| Socket send | 0.237 | 0.056 | 3.361 | 0.271 | 7.596 | 6.53 |

## Acceptance answers (trace-based)

1. **Most expensive operation:** gather_hidden (2.956 ms, 81.45%)
2. **Real serialization time:** 0.0 ms (separate stage present: False)
3. **memcpy time:** 0.003 ms
4. **Repeated allocations per token:** True
5. **Hidden buffer copies:** 2
6. **Socket send significant:** True (avg 0.237 ms)

## Allocation audit (Phase D)

- Samples: 31
- Fresh heap alloc per token: **True**
- capacity_grew events: 31

## Copy path (Phase E)

- Path: `ggml_embeddings->std::vector->kernel_tcp`
- Heap copy count (including TCP read): **2**
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
