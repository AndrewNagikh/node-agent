# Task 15.1b — Hidden Gather Root Cause

**Trace ID:** `trace-000024`
**Status:** PASS
**Steady waves:** 32

## Gather decomposition (trace-based)

**GATHER total:** 5.674 ms

**GPU wait (`LLAMA_BACKEND_SYNCHRONIZE`):** 4.718 ms
**API access (`output_reorder` + pointer):** 0.144 ms
**EMBD D2H async queue (`ggml_backend_tensor_get_async`):** 0.423 ms
**Unattributed:** 0.812 ms

## Stage statistics

| Stage | avg (ms) | min | max | p95 | contribution % |
|-------|---------:|----:|----:|----:|---------------:|
| GATHER (llama_get_embeddings) | 5.674 | 1.974 | 8.056 | 7.557 | None |
| LLAMA_BACKEND_SYNCHRONIZE | 4.718 | 0.956 | 7.008 | 6.967 | 83.15 |
| LLAMA_GET_EMBEDDINGS_ACCESS | 0.144 | 0.055 | 0.29 | 0.233 | 2.54 |
| EMBD_D2H_GET_ASYNC (decode) | 0.423 | 0.168 | 1.695 | 0.678 | 7.46 |
| GGML_GRAPH_EXECUTE | 0.367 | 0.265 | 2.522 | 0.353 | None |

## Acceptance questions

1. **Graph execute vs gather:** wall gap unreliable (clock skew) — GGML_GRAPH_EXECUTE completes during ENTRY_COMPUTE (async). EMBD_D2H_GET_ASYNC is queued at end of decode. llama_get_embeddings() later calls synchronize() to wait for GPU + D2H. Wall-clock gap unreliable on homelab (clock skew) — use span durations.
2. **`ggml_backend_tensor_get`:** True — event `EMBD_D2H_GET_ASYNC`, queue 0.423 ms
3. **GPU synchronize:** True — `LLAMA_BACKEND_SYNCHRONIZE` avg 4.718 ms (metal (entry node-a homelab))
4. **Device→host:** True — ggml_backend_tensor_get_async during decode + completion in synchronize
5. **Wait model:** Graph Execute (async) → EMBD_D2H_GET_ASYNC queued → … CPU / orchestrator gap … → llama_get_embeddings() → LLAMA_BACKEND_SYNCHRONIZE (GPU wait + D2H complete) → LLAMA_GET_EMBEDDINGS_ACCESS (output_reorder, ~0ms) → return pointer
6. **Alternatives:**
   - `llama_get_hidden_state`: Copies hidden_state_inp buffer — input path, not entry output
   - `embd_data_after_sync`: Pointer valid after synchronize; memcpy in COPY stage is redundant if send could use embd.data directly after sync
   - `skip_llama_get_embeddings`: Possible if synchronize()+get_embeddings() split and called once per token after decode

## Verdict

- GPU sync dominates gather: **True**
- gather ≈ sync + access: **False**

## Implication for Task 15.2

If GPU wait ≈ gather, transport zero-copy / FP16 will **not** fix the 5 ms bottleneck. Next lever: overlap synchronize with pipeline, or avoid calling llama_get_embeddings per token.
