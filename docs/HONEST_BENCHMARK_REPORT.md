# Honest Benchmark Report

## Executive summary

The project demonstrated, on three mismatched consumer machines with no
two sharing an OS, GPU vendor, or network medium:

- ✓ 70B-class dense model running end-to-end, no single machine able to
  hold it alone
- ✓ 30B-class MoE model running end-to-end
- ✓ 387 create/generate/destroy cycles across all soak runs, 100% success
  on the final (fixed) run — 190/190, zero crashes, zero corrupted output
- ✓ Speculative decoding, default-on, ×1.64 median speedup on the
  validated pair
- ✓ Up to 98% of the computed theoretical ceiling (70B rung); 32B dense
  and 30B-MoE both clear the project's own ≥80% bar

The rest of this document explains how these numbers were obtained,
where they're thin, and how to reproduce every one of them. Nothing
below is hand-estimated.

## Why this matters

The goal of this project is not to outperform a single powerful GPU. The
goal is to make several heterogeneous consumer machines behave as one
inference runtime. These measurements show the system can sustain
practical inference on models that none of the individual machines could
run alone — that's the claim being tested, and the numbers below are the
test of it.

## Cluster

**3 heterogeneous consumer machines on home LAN/Wi-Fi** —
node-a (Mac, M3 Pro, Metal), node-b (Mac, M1 Pro, Metal, Wi-Fi — the
weakest node), node-c (Windows, RTX 4070 Ti, CUDA). No two nodes share an
OS, GPU vendor, or network medium. Every number below was measured on this
exact cluster, not simulated or scaled from a single-node figure.

This is the G6 gate of `docs/FIRST_SHOWCASE_CRITERIA.md` — the document
this project's showing stands or falls on. Every number below cites its
source report.

## Per-rung results

| Rung | Model | Decode tok/s (production, untraced) | TTFT (ms) | % of computed ceiling | Cold sync | Source |
|---|---|---|---|---|---|---|
| 3B | llama-3.2-3b | **29.5** (n=48) | 228 | 41.1% median (40.0–41.7%, n=3, traced) | ~1 min (920MB, partial reinstall) | [G3 soak](bench/2026-07-23_g3_soak/G3_SOAK_REPORT.md), ceiling this session |
| 14B | qwen3-14b | **16.0** (n=48) | 353 | 62.9% median (59.4–63.5%, n=3, traced) | not freshly timed this project (already installed) | [G3 soak](bench/2026-07-23_g3_soak/G3_SOAK_REPORT.md), ceiling this session |
| 32B dense | qwen2.5-32b | **7.4** (n=47) | 1423 | **86.9% median** (81.2–89.6%, n=3, traced) — **G1 PASS** | ~15-20 min for an 8.7GB partial reinstall this session (not a from-scratch timing) | [G1](bench/2026-07-23_g1_ceiling/G1_CEILING_REPORT.md), [G3 soak](bench/2026-07-23_g3_soak/G3_SOAK_REPORT.md) |
| 30B-MoE (L2) | qwen3-30b | **22.4** (n=47) | 945 | **86.7% median** (60.1–88.5%, n=3, traced) — **G1 PASS** | not freshly timed this project (already installed) | [G1](bench/2026-07-23_g1_ceiling/G1_CEILING_REPORT.md), [G3 soak](bench/2026-07-23_g3_soak/G3_SOAK_REPORT.md) |
| 70B dense (L3, Tier 2) | Llama-3.3-70B-Instruct **Q3_K_S** (not the originally-targeted Q3_K_M — see note) | **2.28** (n=1 plain + 3 traced) | 6698 | **98.4% median** (95.6–101.2%, n=3, traced) | **~42 min** for 30.9GB across 3 nodes in parallel, 726 ops, 0 failures | [L3 report](bench/2026-07-24_l3_70b/L3_70B_REPORT.md) |

**"% of computed ceiling"** always compares measured-vs-ceiling from the
*same single perf-traced run* (never mixing a production number against a
traced ceiling — see the [G1 report](bench/2026-07-23_g1_ceiling/G1_CEILING_REPORT.md)
for why that comparison is invalid). The "decode tok/s (production,
untraced)" column is the real, larger-sample number from the G3 soak
(47-48 cycles per model) or, for 70B, the single plain (non-traced)
measurement from the L3 report — smaller sample, stated as such.

### Why the ceiling ratio drops for smaller/faster models — methodology limitation, not a system problem

The pattern above (3B 41% → 14B 63% → 32B/30B-MoE ~87% → 70B 98%) is
monotonic with speed, not architecture, dense-vs-MoE, or anything about
the models themselves. Perf-trace instrumentation has an unavoidable fixed
per-token overhead even after the flush() fix (see G1 report) — real, but
small in absolute terms. For a slow model (70B: ~440ms/token) that fixed
cost is a rounding error. For a fast model (3B: ~35ms/token even under
trace) the exact same fixed cost eats a large fraction of an already-tiny
window. **The real, production, untraced numbers in the "decode tok/s"
column are unaffected by this** — llama-3.2-3b's real 29.5 tok/s (from 48
soak cycles) is the number that matters for "does the demo feel fast," not
the 41% ceiling ratio, which only means "our measurement instrument
struggles to characterize very fast models cleanly," not "the system is
underperforming."

**G1's own `≥80% of computed ceiling` pass criterion applies only to the
32B dense and 30B-MoE rungs** (`FIRST_SHOWCASE_CRITERIA.md` G1) — both
pass. 3B and 14B are informational rows in this table, not gated; they
were never required to clear 80%.

## Speculative decoding (G2)

Mechanism is default-on, no flags. Measured (64-token runs, same prompt,
Llama-3.2 3B/1B pair): baseline 22.5–27.3 tok/s, speculative 40.8–45.7
tok/s — **×1.64 median** (`TASK_19_SPECULATIVE_PIPELINE_STUDY.md`). The
×2 gate was explicitly not required for this showing
(`FIRST_SHOWCASE_CRITERIA.md`: "×1.5-1.64 measured is enough to show").
SmolLM2/1.7B pair: lower acceptance (~50% at k=4) — pair-dependent, not
universal; stated plainly rather than cherry-picking the better pair.
NORMAL/THROTTLED hysteresis (gates on measured network arrival latency,
not raw hit-rate, so a bad-network window degrades gracefully instead of
collapsing) is implemented and live but hasn't yet been observed
triggering in a real (not manufactured) bad-network window — noted as
open, not claimed.

## Reliability (G3 soak)

30-minute soak, driven through the orchestrator API (same calls the
dashboard makes — dashboard-UI-specific re-confirmation still pending,
see Tier 1 status below), 4 models rotating: **190/190 cycles, 100%
success, zero crashes, zero corrupted output** on the final run, after
fixing two real bugs found by the soak itself (a sequential no-retry
coverage poll, then a full-checksum-reverify-on-every-poll cost) — full
story in the [G3 soak report](bench/2026-07-23_g3_soak/G3_SOAK_REPORT.md).

**387 create/generate/destroy cycles across all three soak runs** (107 +
90 + 190), zero crashes and zero corrupted output in any of them — the
first two runs' failures were all the identical clean 503 from the two
bugs above, never a crash or bad data.

This is NOT a fault-tolerance claim — no node failures were injected — it
is "doesn't fall over under normal repeated use."

## External baseline: prima.cpp

We attempted to prepare a controlled comparison against prima.cpp
([Lizonghang/prima.cpp](https://github.com/Lizonghang/prima.cpp)), the
closest published prior art — pipelined-ring parallelism, mmap disk
offload, and speculative decoding for heterogeneous home clusters. Two
incompatibilities prevented running it on this cluster: it doesn't
support Windows (node-c is Windows), and its supported quantizations
(Q4K/Q6K/Q80/IQ1) don't overlap with what this project uses (Q3_K).
Therefore only a cited comparison is presented, not a measured one.

Their published number for a 70B model: ~2 tok/s (674ms/token TPOT) on a
4-device cluster with weaker GPUs than this project's node-c,
deliberately memory-constrained (37GiB combined RAM+VRAM, by their own
account insufficient for the full quantized model) to exercise their
disk-offload mechanism. This project's 2.28 tok/s on 3 devices with
comfortable memory headroom is in the same ballpark but not a controlled
comparison — full reasoning in the
[L3 report](bench/2026-07-24_l3_70b/L3_70B_REPORT.md#external-reference-point-primacpp-cited-not-run).

## Limitations (stated plainly, per G6's own requirement)

- **Single stream only.** No multi-tenant batching (Task 23, explicitly
  out of scope for this showing).
- **No fault tolerance.** A node dropping mid-session is not handled —
  G3's soak tests repeated normal use, not node churn (Task 22.x, out of
  scope).
- **LAN-only.** Not tested or designed for WAN; node-b is on Wi-Fi within
  the LAN and is the weakest link measured (`homelab-cluster-gotchas`).
- **Slow long-prompt prefill on big models.** TTFT scales with model size
  (228ms at 3B → 6.7s at 70B) — this project's short-prompt numbers above
  don't characterize long-context prefill, which wasn't separately
  benchmarked.
- **Perf-trace ceiling methodology is noisy for fast models** (see above)
  — a real, disclosed measurement-instrument limitation, not swept under
  the rug.
- **Cold sync times are not uniformly fresh-measured.** Only 70B (42min)
  and the 3B/32B partial-reinstall repairs done this session have a
  directly-measured number in this report; 14B and 30B-MoE's original
  installs predate this report's measurement window and aren't re-quoted
  from memory as if freshly timed.
- **Metal memory-budget reporting on the two Mac nodes is a conservative
  software estimate** (Apple's `recommendedMaxWorkingSetSize`), not live
  available memory — found and code-confirmed while debugging the 70B
  quant selection, not yet acted on (flagged as a separate follow-up,
  `docs/bench/2026-07-24_l3_70b/L3_70B_REPORT.md`).
- **qwen3-30b's ceiling ratio has unexplained residual variance**
  (60.1–88.5% across 3 samples, vs qwen2.5-32b's tight 81.2–89.6%) — not
  root-caused; two live hypotheses (MoE expert-routing variance;
  node-b Wi-Fi jitter) neither confirmed.

## Tier 1 gate status (for reference)

| Gate | Status |
|---|---|
| G0 Stranger test | **Dropped 2026-07-24** — no tester available, see `FIRST_SHOWCASE_CRITERIA.md` §4 |
| G1 Capacity proof | **PASSED** (this report) |
| G2 Speculation | Mechanism done, ×1.64 median measured, ×2 gate explicitly not required |
| G3 Demo-grade reliability | **PASSED** via orchestrator API; dashboard-UI re-confirmation still pending |
| G4 Network-aware placement | **DONE** (Task 21.1, live) |
| G5 Reproducibility | **Dropped 2026-07-24** — same reason as G0; bench scripts themselves already exist and were used for every rung in this report |
| G6 Honest benchmark report | **This document** |
| G7 Five-minute explanation | Not written |

Tier 2 (L3-dense, 70B): **ACHIEVED** — see table above.

## Reproduction

Every number in this report has a corresponding reproducible script:

```bash
# Ceiling measurements (any model, same methodology throughout):
python3 docs/bench/2026-07-23_g1_ceiling/measure_ceiling.py <model_id>

# Soak / reliability:
docs/bench/2026-07-23_g3_soak/soak_test_v3.sh

# 70B install + test:
# see docs/bench/2026-07-24_l3_70b/L3_70B_REPORT.md's Reproduction section
```
