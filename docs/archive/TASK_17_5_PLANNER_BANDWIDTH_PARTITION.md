# Task 17.5 — Planner v2: Bandwidth-Proportional Partition

**Type:** Implementation (planner-only; no runtime/protocol changes)
**Status:** Planned
**Parent:** Research 17 (roadmap R5)
**Depends on:** none (independent of 17.1–17.4); measured benefit grows as they land
**Expected gain:** raises every model's ceiling **+23–30%** (Study §9.2); realized gain grows as fixed overheads shrink

---

## Problem

The current planner balances **layer count**, but single-stream pipeline latency is the **sum** `Σ Wᵢ/(η·BWᵢ)` — minimized by placing bytes proportional to node memory bandwidth, subject to memory fit. On this cluster equal-layer TinyLlama ceiling is 162 tok/s vs 199 bandwidth-proportional. Research 17 §10 also derived a node-admission rule the planner currently violates.

## Scope

1. **Cost model:** per-node effective bandwidth `BW_eff` (calibratable constant per node class; default = benchmark score proxy), stage cost = assigned_bytes / BW_eff. Optimize assigned **bytes** (not layer count), honoring:
   - layer granularity (whole layers per stage)
   - endpoint extras: embedding bytes → entry, norm+head bytes → final
   - per-node memory ceilings (weights + KV share + overhead)
2. **Node admission rule:** adding node k+1 to a pipeline reduces latency only if `BW_new > ΣBW_existing / k`. Slower nodes are admitted **only** when required for memory fit, and then receive the smallest share the fit allows.
3. **Memory-constrained placement (70B target — `docs/TARGET_70B_GOAL_AND_FEASIBILITY.md` §4):**
   - per-node **fast-memory** budget (GPU-addressable: unified working set on Metal, VRAM on CUDA) accounting weights share + KV share + backend compute buffers;
   - fill-fastest-first allocation when the model is memory-constrained;
   - **never spill weights to slow (host DDR) memory silently** — refuse the layout with a clear error, or proceed only behind an explicit override flag with the degraded predicted ceiling reported.
4. Planner output must record the predicted per-stage compute ms in the session/runtime graph metadata so traces can be compared against plan.

## Acceptance criteria

| Gate | Threshold | Source |
|------|-----------|--------|
| Unit: heterogeneous 3-node synthetic | fast node gets ∝ BW share; slow node minimal share under fit pressure | planner tests |
| Unit: admission rule | slow node excluded when model fits on faster subset | planner tests |
| Homelab TinyLlama | measured stage compute ratio moves toward plan prediction; Σ stage compute strictly decreases vs equal-layer baseline | trace comparison |
| No memory regressions | all benchmark-matrix models still `fits_cluster` and reach READY | infra-only benchmark run |
| Plan metadata | predicted ms per stage present in session graph | API check |

## Notes

- With today's fixed overheads (~22 ms) the measured TPS change will be modest for small models — the gate is on **stage compute sum**, not headline TPS.
- Keep equal-layer as a fallback policy flag for A/B.

## Non-goals

Dynamic re-partitioning mid-session; role reassignment (Task 11.9); MoE-aware active-bytes modeling (future).

## References

Study §9.1–9.2, §10 (theorem + admission rule); `docs/archive/TASK_11_LAYER_FIRST_RUNTIME.md` §11.5 (Planner v2 groundwork).
