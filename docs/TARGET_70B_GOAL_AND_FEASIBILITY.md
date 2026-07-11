# Project Target — 70B on the 3-Node Homelab Cluster

**Type:** Goal definition + feasibility model (extends Research 17)
**Status:** Adopted target
**Cluster:** M3 Pro (unified, ~14.3 GB GPU-usable) + M1 Pro (~11.5 GB) + RTX 4070 Ti (12 GB VRAM + ~33 GB DDR), ~80 GB total RAM, 1 GbE LAN

---

## 1. The target

> Run a **70B-class model** end-to-end on the 3-node cluster, with **sync, session create, and generation** speed close to a local run (excluding first-time layer download).

Small models (1B-class) remain the **development loop** — fast iteration on protocol/runtime changes. 70B is the **acceptance environment**. Every Task 17.x/18.x gate gets a capacity-ladder counterpart (§6).

## 2. Physics of 70B on this cluster — honest numbers first

**Fast-memory budget** (weights must live in GPU-addressable memory to decode at full speed):

| Node | Fast memory | Eff. bandwidth (Study §9.1) |
|------|------------:|----------------------------:|
| M3 Pro | ~14.3 GB | 90 GB/s |
| M1 Pro | ~11.5 GB | 120 GB/s |
| RTX 4070 Ti | 12 GB VRAM | 302 GB/s |
| **Total fast** | **~37.8 GB** | Σ 512 GB/s |
| 4070 Ti host DDR (spill zone) | ~33 GB | ~35 GB/s [est] |

**Quantization decides everything:**

| Variant | Weights | Fits fast memory? | Decode ceiling [model] |
|---------|--------:|-------------------|-----------------------:|
| 70B Q4_K_M | ~42.5 GB | ❌ ~5–8 GB spills to DDR | **~2.3 tok/s** (spill-dominated) |
| **70B Q3_K_M / IQ3** | ~32–34 GB | ✅ (KV ~1.3 GB @4k ctx also fits) | **~3.5–3.7 tok/s** |
| 70B-class **MoE** (e.g. ~100B-A12B, Q4 ~55–60 GB) | active ~7 GB/token | weights use slow RAM too; active set matters | **~12–14 tok/s [est]** |
| 30B MoE A3B (already in catalog) | ~17.5 GB, active ~2.2 GB | ✅ | **~60 tok/s [est]** |

Ceiling math (dense Q3, memory-constrained placement, fill fastest first): `10/302 + 11/120 + 13/90 ≈ 269 ms/token → 3.7 tok/s`.

**Three consequences:**

1. **"Local" does not exist for 70B** — no single node can hold it. The honest comparison is a *hypothetical* local M3 Pro with enough RAM: `34 GB / 90 GB/s ≈ 378 ms → 2.6 tok/s`. **The cluster ceiling (3.7) is ~40% faster than hypothetical local.** At 70B, distribution stops being a tax and becomes the win — exactly the Study §9.3 amortization prediction.
2. **Today's runtime overhead is already almost irrelevant at 70B.** Fixed per-token tax ~22 ms vs ~270 ms compute → ~92% efficiency *without any Task 17.x work*. The decode-speed work (17.1–17.4) matters for the dev loop and TTFT feel, **not** for 70B throughput.
3. **Absolute speed is bandwidth physics: ~3.5 tok/s dense ceiling.** No implementation work changes it. The only levers above it: **speculative decoding** (Task 19: ×1.5–2.5 → ~6–8 tok/s) and **model choice** (MoE at 70B–100B class → 12+ tok/s feels interactive).

Prefill note: 70B prompt processing is compute-bound; long prompts will take tens of seconds to minutes [est]. Pipeline microbatched prefill (stages overlap across chunks) is the mitigation — unlike decode, prefill *does* parallelize across the pipeline. Candidate follow-up task if TTFT gates fail at 70B.

## 3. What "close to local" means per phase (adopted definitions)

| Phase | Local reference | Target |
|-------|-----------------|--------|
| **Sync (cold, excluded by goal but bounded)** | — | ≥ 70% of 1 GbE wire: 34 GB in ≤ ~7 min (today's path would take hours — Task 18.2) |
| **Session create (warm blobs)** | local mmap load ≈ disk-bound, ~20–30 s for 34 GB NVMe [est] | ≤ 90 s cold workers; **≤ 5 s** with persistent workers (Task 18.1) |
| **Generation** | hypothetical local 2.6 tok/s | **≥ 80% of computed cluster ceiling** (≥ ~3 tok/s dense Q3; ≥ ~6 with speculative) — this *exceeds* hypothetical local |

## 4. Revised priorities (what the 70B goal changes)

**Critical path to the goal (order):**

1. **Task 18.1a — qwen14b session_create fix** — the capacity ladder is blocked at 14B today. Nothing 70B-related can be validated before this.
2. **Task 17.5 (extended) — memory-constrained placement**: planner must (a) fill fast memory fastest-first, (b) **never spill weights to DDR silently** — refuse layout or require explicit flag, (c) account KV + compute buffers per node. This extension is now part of 17.5 scope.
3. **Task 18.2 — sync throughput to wire speed** (new, see task doc) — 50× headroom, hours → minutes.
4. **Task 18.1b–d — persistent workers / event READY / graph-reserve cache** — session create at 70B scale.
5. **Capacity ladder runs** (§6): 14B → 30B-MoE → 70B-Q3.
6. **Task 19 — speculative pipeline** — the only decode multiplier at 70B (draft = 1B same-family; acceptance study must include a 70B target pair).
7. Task 17.1–17.4 continue as **dev-loop quality** work — they keep small-model iteration fast and TTFT low, and their trace gates are how regressions are caught cheaply.

## 5. Risks specific to 70B

| Risk | Mitigation |
|------|------------|
| Q4 doesn't fit fast memory; users expect Q4 quality | Adopt Q3/IQ3 as the dense-70B reference; document quality tradeoff; MoE as the quality-per-speed alternative |
| 4070 Ti: 12 GB VRAM must hold weights share + KV share + CUDA buffers | Planner budget model (17.5 ext) with measured per-backend overhead |
| 80-layer graph reserve / session create time explodes | Graph-reserve cache (18.1d); measure at 30B first |
| Windows node stability under 30+ GB RSS (14B already failed) | 18.1a diagnosability first; soak tests per ladder rung |
| Prefill minutes on long prompts | Microbatched pipeline prefill (follow-up task if gate fails) |

## 6. Capacity ladder — acceptance gates

Each rung: cold sync → warm session → 32-token generate → trace-validated metrics (`PERFORMANCE_METRICS_SPEC.md` discipline).

| Rung | Model | Gate: fits & READY | Gate: session (warm workers) | Gate: decode |
|------|-------|--------------------|------------------------------|--------------|
| L1 | qwen3-14b Q4 (~7.9 GB) | ✅ no spill | ≤ 5 s | ≥ 80% of computed ceiling (~17 tok/s) |
| L2 | qwen3-30b MoE (~17.5 GB) | ✅ | ≤ 10 s | ≥ 80% of MoE ceiling [model TBD in run] |
| L3 | **70B Q3/IQ3 (~34 GB)** | ✅ all weights in fast memory | **≤ 5 s persistent / ≤ 90 s cold** | **≥ 80% of ~3.7 tok/s ceiling; ≥ 6 tok/s with Task 19** |

Ceilings recomputed per actual quant file sizes at run time (planner prediction metadata, Task 17.5) — hand-estimated gates are replaced by computed ones in the run artifacts, per the metrics spec rule.

## 7. References

`docs/DISTRIBUTED_INFERENCE_PERFORMANCE_STUDY.md` §9–10, §13 (ceiling model, amortization, theorem); `docs/archive/LAN_HOMELAB_BENCHMARK_REPORT_20260706.md` (node capacities, 14B failure); Task docs 17.1–17.5, 18.1, 18.2, 19.
