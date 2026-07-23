# First Showcase Criteria (stop-line against infinite optimization)

**Type:** Scope decision / stop-criteria. Written 2026-07-22, revised same
day after review: split into two tiers -- the showcase tests the
**hypothesis** ("is this runtime idea interesting?"), not the product's
completeness. 70B moved out of the blocking gates into its own milestone:
the community's question is "is the idea worth attention?", not "does it
run exactly 70B?" -- a Windows allocator bug at 70B must not be able to
block showing an otherwise-working system.
**Purpose:** define the line where optimization STOPS and showing STARTS.
Anything not listed as a gate here is explicitly NOT required before the
first public showing -- however tempting.
**Inputs:** PROJECT_VISION_DISTRIBUTED_NETWORK.md,
TARGET_70B_GOAL_AND_FEASIBILITY.md,
research/2026-07-22_distributed_inference_survey/, ROADMAP.md Phase 5.5.

---

## 1. What is being shown (the demo story)

One sentence, from the vision: **"Several mismatched consumer machines --
two laptops and a gaming PC -- working as one, running models none of
them could run alone, with one command per node and honest numbers."**

The idea is "home machines work as one", NOT "runs exactly 70B". 70B is
a consequence of the idea (Tier 2 below), not the idea itself. The 32B
dense already qualifies as proof: 18.5 GB of weights fit no single
node's fast memory on this cluster, and it runs at 7+ tok/s today.

The showable artifact is three things together:
1. **A live run**: models that fit on no single node, on the 3-node
   heterogeneous cluster (Metal + Metal + CUDA over home LAN/Wi-Fi),
   driven from the dashboard.
2. **A benchmark write-up** with the honest framing the vision mandates:
   "% of computed cluster ceiling", "vs hypothetical local that doesn't
   exist", an external baseline (prima.cpp, same cluster), and a plain
   limitations section.
3. **A reproducible quickstart** a stranger can follow.

## 2. Tier 1 -- Showcase gates (all must pass; nothing else blocks)

| # | Gate | Pass criterion | Status 2026-07-22 |
|---|------|----------------|-------------------|
| G0 | **Stranger test** | A person who knows nothing about the project can, in one evening: understand from the README why it exists, bring up a cluster on their own 2+ machines, and get a generation out of a model that fits on neither machine alone. If this fails, the other gates matter less. Dry-run it on one real person (or an honest cold read) before publishing. | Not attempted. |
| G1 | **Capacity proof on models that fit no single node** | 32B dense measured (runs today at 7.0-7.5 tok/s; compute its ceiling % for the report) AND the L2-MoE rung (qwen3-30b, catalog) measured -- the "feels fast" demo number. Both: cold sync, warm session, 64-token generation, >=80% of computed ceiling. | **PASSED 2026-07-23** (docs/bench/2026-07-23_g1_ceiling/G1_CEILING_REPORT.md): qwen2.5-32b 86.9% median (81.2-89.6%, 3 clean samples), qwen3-30b 86.7% median (60.1-88.5%, 2/3 samples, more variance -- not yet root-caused). Took 3 real fixes along the way: perf_trace per-event flush() (~6x slowdown), double-counted critical-path formula + inverted clock-skew check, and `/session/destroy` not actually killing workers (contaminated repeated measurements with resource contention). |
| G2 | **Speculation stays on and never hurts** | Default-on speculation (no flags -- done) shows >=x1.5 on at least one pair in the report, AND one recorded bad-network window where THROTTLED engages and degradation is graceful (the 0%-acceptance collapse of 2026-07-21 demonstrably gone). | Mechanism done; x1.5-1.64 recorded; throttle engagement not yet observed in a real bad window -- watch, don't manufacture. |
| G3 | **Demo-grade reliability** | A 30-minute soak: >=20 sequential create/generate/destroy cycles across >=3 models (incl. one 32B+ rung) with zero manual node restarts, driven through the dashboard. NOT fault tolerance -- just "doesn't fall over while someone watches". | **PASSED 2026-07-23** (docs/bench/2026-07-23_g3_soak/): run 3, 190/190 cycles, 100.0% success, zero crashes/corruption. Took 2 fixes: orchestrator's sequential no-retry coverage poll (689a554f6, run1 71%->run2 81%) then node_agent's /installed-layers full-checksum-reverify-on-every-poll (47990afaf, verify-result cache TTL 300s, run2 81%->run3 100%). Caveat: driven via orchestrator HTTP API directly, not yet re-confirmed through the dashboard UI itself. |
| G4 | **Network-aware placement (Task 21.1)** | Entry role never lands on the worst-RTT node when an alternative fits. Small, already planned with code anchors, removes the most likely live-demo embarrassment. | Planned (tracker #20). |
| G5 | **Reproducibility** | README quickstart: a fresh machine joins in <=10 minutes with `run-agent.sh NODE_ID=x` (or .ps1); bench scripts in-repo regenerate every table in the report. | Scripts exist; README quickstart needs a pass. |
| G6 | **The honest benchmark report** | One document: per-rung table (3B / 14B / 32B / 30B-MoE, plus Tier 2 rungs if reached) with tok/s, % of computed ceiling, spec on/off where a pair exists, TTFT, sync time; **prima.cpp on the same cluster** at the rungs actually achieved; limitations stated plainly: single stream, no fault tolerance, LAN-only, slow long-prompt prefill on big models. | **DONE 2026-07-24**: `docs/HONEST_BENCHMARK_REPORT.md`. All 5 rungs (3B/14B/32B/30B-MoE/70B) with real tok/s + ceiling % + TTFT; prima.cpp cited (not run, see G6 doc for why); spec ×1.64 median cited; limitations section incl. the newly-found ceiling-methodology weakness for fast models and the Metal memory-budget quirk. |
| G7 | **Five-minute explanation** | The problem, the solution, the limitations, and the numbers explainable to an engineer in five minutes (this doubles as the post's opening). If it can't be done, the architecture story hasn't settled -- fix the story, not the code. | Not written. |

## 3. Tier 2 -- Milestone L3: maximum-capacity proof (ideal for the showcase, not blocking)

Strongly desired for the first showing -- "we ran a 70B-class model on
this junk" is the headline that travels -- but its failure does NOT
block publishing Tier 1. If it lands, it leads the report; if it
doesn't, the report says "L3 in progress" with the measured blocker.

| Rung | Target | Pass criterion | Status |
|------|--------|----------------|--------|
| L3-dense | Llama-3.3-70B-Instruct Q3_K_M (~34 GB, fits the ~37.8 GB fast-memory budget; same-family 1B draft enables the spec stretch) | cold sync bounded (~34 GB at wire speed ~7-10 min; hours = revisit Task 18.2 first), warm session, >=80% of the computed ~3.7 tok/s ceiling | **ACHIEVED 2026-07-24** (docs/bench/2026-07-24_l3_70b/L3_70B_REPORT.md): Q3_K_M didn't fit the cluster's currently-free memory (~37.3GB budget vs ~40GB needed) -- switched to Q3_K_S (30.9GB), which fit. Cold sync ~42min (not the doc's optimistic 7-10min, but well inside the "hours" escape hatch). Warm session create 9s. Decode ~2.28 tok/s, ceiling (same perf-trace methodology as G1) 95.6-101.2% across 3 samples, median 98.4% -- comfortably passes. Coherent, factually-correct output. |
| L3-MoE (stretch) | 70B-100B-class MoE with small active set (TARGET_70B §2 models ~100B-A12B at ~12-14 tok/s [est] using the 4070 Ti's DDR as the inactive-expert zone; ~80 GB total cluster RAM fits it) | generates end-to-end; report actual tok/s vs the MoE ceiling computed for the real file. If it works, this is the single most impressive demo number the hardware allows. | Not attempted. |

Known L3 risks (TARGET_70B §5): 4070 Ti VRAM budget with KV+CUDA
buffers, Windows RSS stability at 30+ GB, session-create time,
long-prompt prefill. Hit them at 30B-MoE first (G1) where debugging is
cheaper.

## 4. Explicitly NOT required before showing (the discipline list)

- **The x2 speculation gate.** x1.5-1.64 measured is enough to show.
- **Task 21.2 (direct token return), 21.4/21.5 (measured partitioning).**
  Real levers, not showstoppers. Do after, driven by feedback.
- **Task 20 (tree/ensemble speculation).** Research track, own go/no-go.
- **Fault tolerance / node churn (Task 22.x).** G3's soak is the bar.
- **Multi-tenant batching (Task 23), WAN, trust/isolation, incentives.**
- **Dashboard polish / Windows packaging.** Must survive G3 on the Mac.
- **RFC-0014 write-up, SPEC_DEBUG cleanup, refactoring** not forced by
  a gate.

Rule: if a work item doesn't unblock a gate in §2 (or the Tier 2
attempt), it waits until after the showing -- no exceptions without
editing this file first.

## 5. Order of remaining work (maps to tracker)

1. **Task 21.1** (tracker #20) -- small, protects the live demo (G4).
2. **L2-MoE rung** (G1): install qwen3-30b, measure; absorbs the
   session-create-at-scale risks cheaply.
3. **Tier 2 attempt**: L3-dense sync + run; L3-MoE stretch if dense
   lands without draining the budget. Timebox it -- if it eats more
   than ~2-3 sessions of debugging, publish Tier 1 and continue L3
   after, per the tier split's whole point.
4. **G3 soak** through the dashboard; fix only what breaks it.
5. **prima.cpp baseline** at the achieved rungs.
6. **Report (G6) + quickstart (G5) + five-minute story (G7) + stranger
   test (G0)**, publish.

Estimate: Tier 1 alone ~4-6 focused sessions; Tier 2 adds 1-3 more,
timeboxed.

## 6. What "worth attention" means (the decision after showing)

The showing answers one question cheaply: does anyone besides us want
this? Pre-commit the evaluation so the answer is read honestly -- and
judge by the QUALITY of feedback, not the quantity. Open source
visibility is noisy: an excellent project can simply go unseen, so
silence alone is NOT a stop signal -- it first means "distribution
failed", not "idea failed".

- **Where:** r/LocalLLaMA and/or HN with the report; repo public with
  the quickstart. If the first post sinks without engagement, try at
  least one more channel/timing before reading anything into it.
- **Strong positive signals (a handful of these outweigh a hundred
  likes):** one independent reproduction on someone else's hardware; a
  substantive issue from a real attempt; a planner/architecture
  improvement proposal; "how do I adapt this to my X?" that converts
  into an issue or PR.
- **Weak signals (nice, not decisive):** stars, upvotes, comments that
  don't touch the substance.
- **Negative signal:** engaged technical readers conclude "prima.cpp/
  Petals/exo already covers this" and no counter-story survives contact.
  That is a verdict on the hypothesis, not on distribution.
- **On stop:** even then, the honest moves bank everything: upstream
  the pieces the survey found no analogs for (adaptive wait window,
  throttle hysteresis), write up the findings, keep the cluster as a
  personal tool without the network-era investment.

## 7. Relationship to the roadmap

This file gates ROADMAP Phase 5.5 -> "showcase" and defers Phase 6
(network era) until AFTER the §6 decision. TARGET_70B §6's ladder
remains the underlying acceptance mechanics; this file fixes which
rungs block the first showing (Tier 1: 32B + 30B-MoE) and which are the
milestone attempt (Tier 2: L3), and freezes everything else.
