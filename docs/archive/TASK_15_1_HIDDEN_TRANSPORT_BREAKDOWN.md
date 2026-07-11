# Task 15.1 — Hidden Transport Breakdown (A→B)

**Trace ID:** `trace-000024`
**Status:** PASS
**Total pack time:** **5.856 ms** (HIDDEN_PACK_TOTAL_END)

## Hidden Pack

**Total:** 5.856 ms

**Allocation:** 0.003 ms
**Gather hidden:** 5.713 ms
**Copy (memcpy):** 0.003 ms
**Serialization:** 0.0 ms
**Frame build:** 0.119 ms
**Socket send:** 0.056 ms

## Stage statistics

| Stage | avg (ms) | min | max | p95 | total (ms) | contribution % |
|-------|---------:|----:|----:|----:|-----------:|---------------:|
| Allocation | 0.003 | 0.002 | 0.005 | 0.004 | 0.09 | 0.05 |
| Gather hidden | 5.713 | 1.974 | 8.056 | 7.557 | 177.11 | 97.56 |
| Copy (memcpy) | 0.003 | 0.001 | 0.027 | 0.009 | 0.094 | 0.05 |
| Serialization | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Frame build | 0.119 | 0.013 | 0.193 | 0.167 | 3.812 | 2.03 |
| Socket send | 0.056 | 0.029 | 0.115 | 0.095 | 1.806 | 0.96 |

## Acceptance answers (trace-based)

1. **Most expensive operation:** gather_hidden (5.713 ms, 97.56%)
2. **Real serialization time:** 0.0 ms (separate stage present: False)
3. **memcpy time:** 0.003 ms
4. **Repeated allocations per token:** True
5. **Hidden buffer copies:** 2
6. **Socket send significant:** False (avg 0.056 ms)

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
