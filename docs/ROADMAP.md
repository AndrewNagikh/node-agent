# Engineering Roadmap — Distributed Inference Network

**Status:** Adopted 2026-07-11 (fixed with Research 17)
**North star:** `PROJECT_VISION_DISTRIBUTED_NETWORK.md` — capacity network on commodity hardware
**Near-term acceptance:** `TARGET_70B_GOAL_AND_FEASIBILITY.md` — 70B on the reference 3-node cluster
**Evidence base:** `DISTRIBUTED_INFERENCE_PERFORMANCE_STUDY.md` (Research 17); Task 11–16 archives in `docs/archive/`

---

## Success metrics (in priority order)

1. **Largest servable model** on a given node pool (capacity ladder)
2. **% of computed cluster ceiling** for decode (never "% of local" — Study §10 theorem)
3. **Warm session create time**; cold sync vs wire floor
4. **Aggregate tok/s at N streams** (from Phase 6)
5. **Survival under node churn** (from Phase 6)

## Retired directions (do not reopen — Study evidence)

Wire format / FP16 hidden / binary protocol / zero-copy / TCP tuning (< 1% each); tensor parallel over 1 GbE (latency floor worse than today's pipeline); "% of local" as a KPI for models that fit on one node.

---

## Phases

### Phase 0 — Measure & Unblock *(current; all parallelizable, no runtime risk)*

| Item | Task | Exit |
|------|------|------|
| Bubble attribution: are v2 flags live on homelab? where do 11.7 ms go? | [17.1 Phase A](TASK_17_1_ORCHESTRATOR_BUBBLE_CLOSURE.md) | ≥90% of bubble attributed |
| qwen14b session_create 500 + error persistence | [18.1a](TASK_18_1_FAST_SESSION_LIFECYCLE.md) | 14B generates 32 tokens |
| Sync path attribution (1.3–14 MiB/s vs ~110 wire) | [18.2 Phase A](TASK_18_2_SYNC_THROUGHPUT.md) | ≥90% of sync wall attributed |

### Phase 1 — Decode overhead (dev-loop speed, TTFT)

Order: **17.1B → 17.3**, 17.2 independent. Trajectory (homelab TinyLlama): 25.8 → ~37 ([17.1](TASK_17_1_ORCHESTRATOR_BUBBLE_CLOSURE.md)) → ~44 ([17.2](TASK_17_2_SAMPLING_RETURN_PATH.md)) → ~50 tok/s ([17.3](TASK_17_3_GPU_SYNC_OVERLAP.md)). Gates: bubble <10% (RFC-0013 §28), sampler ≤1.5 ms, gather ≤1.5 ms, determinism preserved.

### Phase 2 — Startup & placement (critical path to 70B)

| Item | Task | Gate |
|------|------|------|
| Persistent workers, event READY, graph-reserve cache | [18.1b–d](TASK_18_1_FAST_SESSION_LIFECYCLE.md) | warm session ≤2 s (1B) / ≤5 s (8B) |
| Sync at wire speed | [18.2 Phase B](TASK_18_2_SYNC_THROUGHPUT.md) | 4.7 GB ≤2.5 min; 34 GB ≤10 min |
| Planner: bytes ∝ bandwidth, node-admission rule, fast-memory budget, **no silent DDR spill** | [17.5](TASK_17_5_PLANNER_BANDWIDTH_PARTITION.md) | plan-vs-trace prediction recorded |

### Phase 3 — Capacity ladder (acceptance track)

Per `TARGET_70B_GOAL_AND_FEASIBILITY.md` §6: **L1** qwen3-14b → **L2** 30B-MoE → **L3** 70B Q3/IQ3 (~34 GB in 37.8 GB fast memory). Each rung: cold sync → warm session → 32 tokens → ≥80% of computed ceiling, all trace-validated. **Plus external baseline: prima.cpp on the same cluster at L2/L3** (vision doc §7).

### Phase 4 — Endpoint compute inflation *(parallel with Phase 3)*

[17.4](TASK_17_4_ENDPOINT_COMPUTE_INFLATION.md): measured breakdown of entry 3.5× / final 5.7× inflation, then graph capture/reuse behind a flag. Target ~70–75 tok/s TinyLlama (~55–60% of local — the architecture's single-stream limit, Study §13).

### Phase 5 — Speculative decoding (the only multiplier) — **DONE (mechanism), gate open**

[Task 19](TASK_19_SPECULATIVE_PIPELINE_STUDY.md) shipped 2026-07-19..22: entry-buffered
draft over a direct fa-link, batched verify waves, adaptive P80 wait window
(`SPEC_WAIT_POLICY`), NORMAL/THROTTLED hysteresis, per-session auto-enable (no env
flags), network observability (`/network/stats`). Measured ×1.5–1.64 (SmolLM2 and
Llama-3.2 pairs; baseline bench in `bench/2026-07-21_wait_policy/`). The ×2 gate is
NOT yet passed — carried into Task 21 verification. RFC-0014 write-up deferred.

### Phase 5.5 — Active plans *(current)*

| Plan | Scope | Doc |
|------|-------|-----|
| Task 21 | Adopt proven practices (Petals et al.): network-aware role placement, direct final→client token return, measured layer partitioning, relayout hysteresis | [TASK_21_PROVEN_PRACTICES_PLAN.md](TASK_21_PROVEN_PRACTICES_PLAN.md) |
| Task 20 | Tree/ensemble speculative decoding (go/no-go gated on Phase 0 offline measurement) | [TASK_20_TREE_SPECULATIVE_PLAN.md](TASK_20_TREE_SPECULATIVE_PLAN.md) |

Prior-art grounding for both: [research/2026-07-22_distributed_inference_survey/](research/2026-07-22_distributed_inference_survey/SURVEY.md).

### Phase 6 — Network era *(specify after L3 passes; design research may start earlier)*

| Candidate | Scope |
|-----------|-------|
| Task 22.1 | Fault tolerance: layer replication, session re-planning, KV recovery (re-prefill vs checkpoint; study Petals' dual client/server KV cache first — survey part2 §2.8) |
| Task 22.2 | Dynamic membership: join/leave, background redistribution |
| Task 23 | Multi-tenant continuous batching (aggregate tok/s KPI) |
| — | Node classes Anchor/Capacity/Utility in planner (vision §3); microbatched pipeline prefill if 70B TTFT gate fails |

*(Renumbered 2026-07-22: former "Task 20.1/20.2/21" placeholders became 22.1/22.2/23,
since Task 20 and 21 numbers were taken by the active plans above.)*

---

## Dependency graph

```
Phase 0:  17.1A ──► 17.1B ──► 17.3          18.1a ──► ladder L1
          18.2A ──► 18.2B ─────────┐
                    17.2 (indep)   ├──► Phase 3 ladder L2 ──► L3 ──► Phase 6
                    17.5 (indep) ──┘              ▲
Phase 4:  17.4 (after 17.1–17.3 baselines) ──────┘ (dev-loop quality)
Phase 5:  19 research (anytime) ──► 19 impl (best after Phase 1)
```

## Working rules

- Every task: measurement/attribution phase gates the implementation phase (Task 15/16 discipline).
- All performance claims via `PERFORMANCE_METRICS_SPEC.md` artifacts; ceilings computed, never hand-estimated.
- New runtime behavior lands behind flags, v2-default-off until parity gates pass; determinism (same seed → same tokens) is a standing gate.
- Small models remain the dev loop; ladder rungs are the acceptance environment.
