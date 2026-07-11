# Task 17.4 — Endpoint Compute Inflation Study & Graph Reuse

**Type:** Research (Phase A, mandatory) → Implementation (Phase B, gated on A)
**Status:** Planned
**Parent:** Research 17 (roadmap R4)
**Depends on:** Tasks 17.1–17.3 (so remaining critical path is compute-dominated)
**Expected gain:** +10–20% [est] — attacks the ~9–11 ms endpoint inflation; path to ~70–75 tok/s

---

## Problem

Research 17 §6.3: measured stage compute vs bandwidth-model floor —

| Stage | Node | Measured | Floor [model] | Ratio |
|-------|------|---------:|--------------:|------:|
| Entry (embed + ~8 layers) | M3 Pro / Metal | 8.79 ms | ~2.5 ms | **3.5×** |
| Middle (~7 layers) | M1 Pro / Metal | 2.21 ms | ~1.86 ms | **1.2×** |
| Final (~7 layers + head) | 4070 Ti / CUDA | 5.68 ms | ~1.0 ms | **5.7×** |

The middle stage proves GGML layer kernels are near physics; the inflation lives in **endpoint responsibilities** (graph submit, batch prep, embedding path, D2H staging, logits). The composition is currently **inferential** — this task makes it measured before anything is changed.

## Phase A — Measured breakdown (no runtime changes)

Instrument entry and final compute windows into sub-spans:

1. batch/ubatch preparation (CPU)
2. graph build vs graph cache hit; `ggml_backend_sched_alloc_graph`
3. graph submit (`GGML_GRAPH_EXECUTE` CPU side) vs actual GPU busy time (Metal GPU timestamps / CUDA events where available)
4. embedding input path (entry) / logits + output-head handling (final)
5. output buffer staging (D2H queue on entry, logits materialization on final)
6. kernel-count / command-buffer-count per token per stage

**Exit:** ≥ 85% of entry and final spans attributed; a verdict per component: *CPU submit overhead* vs *GPU under-utilization (small kernels)* vs *staging*.

## Phase B — Reduce (scope chosen from Phase A)

Candidates, strictly data-driven:

- graph/command-buffer reuse across tokens (decode graph topology is static per session): Metal command-buffer / CUDA Graph capture-replay
- eliminate per-token graph rebuilds and allocations on the decode path (`n_ubatch=1` shape pinned)
- batch-prep fast path for single-token decode waves

This phase may require llama.cpp/GGML-adjacent changes — it is the first task in the roadmap allowed to touch that layer, and must stay behind a runtime flag with v2-default-off until parity gates pass.

## Acceptance criteria

| Gate | Threshold | Source |
|------|-----------|--------|
| Phase A attribution | ≥ 85% of entry & final spans | breakdown artifact |
| Entry compute (steady decode) | ≤ 2× bandwidth floor (≤ ~5 ms) | trace |
| Final compute | ≤ 2× bandwidth floor (≤ ~2 ms) | trace |
| Token parity | same seed → same tokens, flag on vs off | parity test |
| TPS | consistent with span reductions ±10% | `validation.json` |

## Non-goals

Model/quantization changes; partition changes (17.5); middle-stage work (already at 84% efficiency).

## References

Study §6.3, §9.1, §12; `docs/archive/TASK_15_1b_HIDDEN_GATHER_ROOT_CAUSE.md` (span methodology); `docs/archive/TASK_16_END_TO_END_TOKEN_COST_MODEL.md` §1.2.
