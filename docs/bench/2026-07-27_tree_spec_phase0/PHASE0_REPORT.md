# Task 20 Phase 0 — result and decision

**Date:** 2026-07-28
**Pair:** Llama-3.2-3B-Instruct-Q4_K_M (target) + Llama-3.2-1B-Instruct-Q4_K_M (draft)
**Sample:** 6 prompts x 48 steps = 288 greedy steps, single machine, no cluster
**Harness:** [measure_topk_acceptance.cpp](measure_topk_acceptance.cpp)

## Decision: NO-GO. Task 20 stops here.

Projected `throughput_ratio` is **0.21** for the width-2 tree the plan
targets, and **0.57** for the smallest prototype the plan allows (depth 2,
branching 2). The plan's own table says stop below 1.10. This is not a
near miss.

The blocker is not acceptance — acceptance is healthy. It is that **this
project's verify wave is sequential**, so a tree pays full price for every
extra candidate.

## What was measured

| | acceptance | E (tokens/wave, k=4) | E-ratio |
|---|---|---|---|
| draft top-1 (linear, today) | 80.21% | 3.375 | — |
| target token within top-2 | 92.01% | 4.263 | **1.263** |
| target token within top-3 | 95.49% | 4.569 | **1.354** |
| not in top-4 at all | 4.51% | | |

Per-prompt top-1 ranged 66.7%–95.8%. The spread is real, but every single
prompt gained from width 2 — the effect isn't carried by one outlier.

Rank 3 contributed **0.00%**. Beyond top-3 there is nothing to collect, so
width 3 is the widest tree that could ever matter for this pair.

**Cross-check:** Task 19 §E measured this same pair offline at 83.3% (k=2)
and 77.7% (k=4). Those are wave-level rates that already fold in
compounding; this measurement is the underlying single-step probability, so
landing at 80.2% between them is the expected result and indicates the
harness agrees with the existing one.

## Why it fails anyway

`E` is only the numerator. The plan (§0.5) is explicit that the criterion is
`E / T_verify`, and it estimated `T_verify_tree` would grow **sub-linearly**
in candidate count, reasoning that decode is weight-bandwidth-bound and a
batch reads the weights once.

**That premise is contradicted by our own earlier measurement.** Task 19 §D,
"Correction to §D — sequential verify decode":

> Hidden-state injection on stages with `layer_start > 0` must run one token
> at a time (KV per position), so a verify wave costs ~(k+1) stage-computes,
> not ~1. Only the fixed cost F amortizes.

The batch that was supposed to make trees cheap does not exist on our
middle and final stages. Verify cost scales **linearly** in the number of
candidate tokens. A tree multiplies candidates, so it multiplies cost.

| shape | candidates | T-ratio | E-ratio | projected throughput_ratio |
|---|---|---|---|---|
| linear k=4 (today) | 5 | 1.0 | 1.000 | 1.00 |
| tree w2 d2 (min prototype) | 6 vs 3 | 2.0 | 1.131 | **0.57** |
| tree w2 d4 | 30 | 6.0 | 1.263 | **0.21** |
| tree w3 d4 | 120 | 24.0 | 1.354 | **0.06** |

Break-even, from the harness: width 2 needs verify cost to stay under
**1.148x** to reach the 1.10 prototype band, and under **0.971x** — cheaper
than linear, while carrying 6x the tokens — to reach full go. Against an
actual T-ratio of 6.0.

Put plainly: speculation works on this cluster because the fixed per-wave
network cost `F` is large and gets amortized across accepted tokens. A tree
inflates the compute term `C` several-fold while `F` stays constant, so it
eats exactly the amortization that made speculation pay in the first place.

## The prerequisite, if this is ever revisited

Not more acceptance — more acceptance is available and it isn't enough.
The prerequisite is the runtime upgrade Task 19 §D already named as "the
single most valuable runtime upgrade": **KV-correct batched hidden-state
injection**, removing the one-token-at-a-time constraint on stages with
`layer_start > 0`.

Only after that does the plan's sub-linear premise become true and the
table above worth recomputing. Even then, width 2 needs T-ratio < 1.148 for
6x the tokens to clear the *prototype* bar; the >= 1.30 "full go" band needs
verify to get cheaper than linear and is unreachable on this pair regardless.

That upgrade also directly speeds up the linear speculation already in
production, which is a better reason to do it than trees are.

## Scope of this result

- **One pair.** Acceptance is strongly pair-dependent — this project has
  measured 80% here and 19% on Llama-3.3-70B + 1B. But the arithmetic fails
  by 5x, and no plausible acceptance rescues it: even a perfect draft
  (100%, E = 5.0) gives E-ratio 1.48 against T-ratio 6.0.
- **Tree node counts assume full branching** at every level. Pruned trees
  (Medusa/SpecInfer style) carry fewer candidates — but they also collect
  less than the measured ceiling, since 92.01% is what width 2 delivers only
  when it branches everywhere. Pruning moves both numerators and
  denominators down; it does not close a 5x gap.
- Greedy decoding only. Under temperature sampling the acceptance test is
  probabilistic and the numbers would differ, but the cost side is unchanged.
