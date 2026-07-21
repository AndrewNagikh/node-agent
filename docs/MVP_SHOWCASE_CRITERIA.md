# MVP: minimal readiness to show the project (stop-criteria)

**Type:** Scope decision / stop-criteria. Written 2026-07-22.
**Purpose:** define the line where optimization STOPS and showing STARTS, so
the project doesn't disappear into infinite tuning. Anything not listed as a
gate here is explicitly NOT required before the first public showing --
however tempting.
**Inputs:** PROJECT_VISION_DISTRIBUTED_NETWORK.md (the story and defensible
slot), TARGET_70B_GOAL_AND_FEASIBILITY.md (capacity ladder + computed
ceilings), research/2026-07-22_distributed_inference_survey/ (what to
honestly claim vs not), ROADMAP.md Phase 5.5 (open tasks).

---

## 1. What is being shown (the demo story)

One sentence, from the vision: **"Three mismatched consumer machines --
two laptops and a gaming PC -- running models none of them could run
alone, with one command per node and honest numbers."**

The showable artifact is three things together:
1. **A live run**: 70B-class dense + a 30B MoE on the 3-node cluster
   (M3 Pro + M1 Pro + RTX 4070 Ti over home LAN/Wi-Fi), driven from the
   dashboard.
2. **A benchmark write-up** with the honest framing the vision mandates:
   never "as fast as local" -- always "% of computed cluster ceiling"
   and "vs hypothetical local that doesn't exist", plus an external
   baseline (prima.cpp, same cluster).
3. **A reproducible quickstart** so a stranger with 2-3 spare machines
   can try it.

The audience judges exactly one claim: *capacity on junk hardware at
usable speed with a real control plane*. Not raw speed, not fault
tolerance, not a network of strangers.

## 2. Hard gates (all must pass; nothing else blocks)

| # | Gate | Pass criterion | Status 2026-07-22 |
|---|------|----------------|-------------------|
| G1 | **Capacity ladder to L3** | 70B Q3/IQ3 (~34 GB): cold sync completes, warm session starts, 64-token generation at **>=80% of the computed ~3.7 tok/s ceiling** (>= ~3 tok/s). Plus L2-MoE rung (qwen3-30b, catalog) -- the "feels fast" demo number. 32B dense already runs (7.0-7.5 tok/s; compute its ceiling % during the report). | **Open -- the single biggest missing piece.** 70B has never been attempted on this cluster. |
| G2 | **Speculation stays on and never hurts** | Default-on speculation (no flags -- done) shows >=x1.5 on at least one model pair in the report, AND one recorded bad-network window where THROTTLED engages and tok/s degrades gracefully instead of collapsing (the 0%-acceptance failure mode of 2026-07-21 must be demonstrably gone). | Mechanism done; x1.5-1.64 recorded. Throttle engagement not yet observed in a real bad window -- watch for one, don't manufacture one. |
| G3 | **Demo-grade reliability** | A 30-minute soak: >=20 sequential create/generate/destroy cycles across >=3 models (incl. one 32B+ rung) with zero manual node restarts, driven through the dashboard. NOT fault tolerance -- just "doesn't fall over while someone watches". | Not yet run as a protocol. |
| G4 | **Network-aware placement (Task 21.1)** | Entry role never lands on the worst-RTT node when an alternative fits. Included in MVP because it's small, already planned with code anchors, and removes the most likely live-demo embarrassment (Wi-Fi entry node in a bad window). | Planned (tracker #20). |
| G5 | **Reproducibility** | README quickstart: a fresh machine joins the cluster in <=10 minutes with `run-agent.sh NODE_ID=x` (or .ps1); bench scripts in-repo regenerate every table in the report. | Scripts exist (docs/bench/); README quickstart needs a pass. |
| G6 | **The honest benchmark report** | One document: per-rung table (3B / 14B / 32B / 30B-MoE / 70B) with tok/s, % of computed ceiling, spec on/off where a pair exists, TTFT, sync time; **prima.cpp on the same cluster at L2/L3** as the external baseline (vision §7 action item); a limitations section stating plainly: single stream, no fault tolerance, LAN-only, prefill on long prompts is slow at 70B. | Not started; all methodology exists (bench protocol + PERFORMANCE_METRICS_SPEC discipline). |

## 3. Explicitly NOT required before showing (the discipline list)

Written down so future sessions don't relitigate:

- **The x2 speculation gate.** x1.5-1.64 measured is enough to show; x2
  is a research goal that Task 21.2/20 may or may not reach later. The
  report states the measured number, not the aspiration.
- **Task 21.2 (direct token return), 21.4/21.5 (measured partitioning).**
  Real levers, not showstoppers. Do after, driven by feedback.
- **Task 20 (tree/ensemble speculation).** Research track with its own
  go/no-go; zero bearing on MVP.
- **Fault tolerance / node churn (Task 22.x).** Vision-critical for the
  *network era*, not for a 3-node showcase. G3's soak is the bar.
- **Multi-tenant batching (Task 23), WAN, trust/isolation, incentives.**
- **Dashboard polish / Windows packaging.** Dashboard must survive G3 on
  the Mac; that's all.
- **RFC-0014 write-up, SPEC_DEBUG cleanup, any refactoring** not forced
  by G1-G6.

Rule: if a work item doesn't unblock a gate in §2, it waits until after
the showing -- no exceptions without editing this file first.

## 4. Order of remaining work (maps to tracker)

1. **Task 21.1** (tracker #20) -- small, protects the live demo.
2. **L2-MoE rung**: install qwen3-30b (catalog), measure. First
   session-create at this scale may surface the graph-reserve risks
   from TARGET_70B §5 -- better to hit them at 30B than at 70B.
3. **L3 rung**: pick the 70B model (Llama-3.3-70B-Instruct Q3_K_M
   ~34 GB is the reference; same-family 1B draft enables the spec
   stretch goal), sync (~34 GB over 1 GbE: expect ~7-10 min at wire
   speed -- if it takes hours, Task 18.2's sync path needs a look
   before proceeding), generate, measure against the computed ceiling.
   Known risks (TARGET_70B §5): 4070 Ti VRAM budget with KV+CUDA
   buffers, Windows RSS stability, session-create time.
4. **G3 soak** through the dashboard; fix only what breaks it.
5. **prima.cpp baseline** on the same cluster at L2/L3.
6. **Write the report** (G6), quickstart pass (G5), publish.

Estimate: ~5-8 focused sessions, dominated by L3 bring-up unknowns.

## 5. What "worth attention" means (the decision after showing)

The showing exists to answer one question cheaply: does anyone besides
us want this? Pre-commit the evaluation so the answer is read honestly:

- **Where:** r/LocalLLaMA and/or HN post with the report; the repo
  public with the quickstart.
- **Positive signal (continue toward the network era):** at least one
  independent reproduction on someone else's hardware, or concrete
  "can I run this on my X?" engagement that converts into an issue/PR,
  or the prima.cpp comparison drawing technical discussion.
- **Negative signal (stop scaling ambitions):** silence, or "why not
  just prima.cpp/Petals/exo" with no counter-story surviving contact.
  In that case the honest moves are: upstream the reusable pieces
  (adaptive wait window, throttle hysteresis -- the survey found no
  analogs), write up the findings, and keep the cluster as a personal
  tool without the network-era investment.
- Either way, the project banks: a working system, a defensible survey,
  reproducible benchmarks, and public evidence of engineering quality.

## 6. Relationship to the roadmap

This file gates ROADMAP Phase 5.5 -> "showcase" and defers Phase 6
(network era) until AFTER the §5 decision. The capacity ladder gates in
TARGET_70B §6 remain the underlying acceptance mechanics; this file
only fixes WHICH of them block the first showing (L2-MoE, L3) and
freezes everything else.
