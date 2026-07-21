# Task 20 -- Tree/Ensemble Speculative Decoding (plan)

Status: PLANNING, not started. Written 2026-07-22 as a follow-on to Task 19
(linear speculative decoding, entry-buffered draft, adaptive wait window).
Task 19's mechanism stays as-is and in production use (SPEC_WAIT_POLICY=p80
default, NORMAL/THROTTLED hysteresis) -- this is a new, larger initiative
layered on top of it, not a replacement.

## 0. Why this direction over alternatives

Discussed and rejected: tensor-parallel-style sub-layer splitting (attention/
MLP/norm as independently placed blocks). Pipeline parallelism (current
design, one node per contiguous layer range) costs ~2 network round trips
per token. Tensor parallelism needs a synchronization point after every
attention and MLP block -- for a 64-layer model that's roughly 128 sync
points per token, two orders of magnitude more network traffic. That trade
only makes sense with sub-microsecond interconnects (NVLink/InfiniBand);
over a home LAN/Wi-Fi (measured RTT 10-50ms between our nodes) it would be
strictly worse than what we have. Not pursuing it.

Tree/ensemble speculative decoding doesn't increase synchronization
frequency -- it increases useful work done between the same number of
syncs (one batched verify call still resolves an entire wave, whether that
wave is a straight line of k tokens or a tree of candidates). That's
compatible with the constraint that actually matters here (network cost),
so this is the direction worth building out.

There is also a natural fit with this project's own diagnosed problem: a
single-stream pipeline leaves two of three nodes idle most of the time
(only one node computes per token; see the Qwen2.5-32B throughput
discussion, 2026-07-22). Tree/ensemble drafting is a way to spend that
otherwise-wasted idle time on candidate generation instead of nothing.

## 0.5 Central hypothesis: what actually has to improve

The naive framing -- "a tree gives more acceptance, so it's faster" -- is
wrong, and treating it as the project's success criterion is the single
biggest risk in this plan. Acceptance length is not the thing that
determines speedup; **tokens produced per unit of verify time is**:

```
throughput_ratio(strategy) = E(strategy) / T_verify(strategy)
```

where `E` is expected accepted tokens per wave and `T_verify` is the wall-
clock cost of the batched verify call that resolves that wave. A tree only
earns its complexity if:

```
E_tree(N) / T_verify_tree(N)   >   E_linear(k) / T_verify_linear(k)
```

by a real margin -- not if `E_tree > E_linear` alone. Worked example, same
shape as the illustration this section is named after: verify cost goes
from 18ms (linear) to 26ms (tree, +44%) and acceptance goes from 1.0
tokens/wave to 1.3 (+30%). Ratio: `1.3/26 = 0.050` vs `1.0/18 = 0.056` --
the tree is **slower** despite strictly higher acceptance, because verify
cost grew faster than acceptance did. This is not a corner case to guard
against after the fact; it is the thing Phase 0 exists to check.

**Why there's reason for cautious optimism on `T_verify_tree`, but not
certainty**: decode-time verify cost on our hardware is dominated by
reading model weights (see the 2026-07-22 Qwen2.5-32B bandwidth analysis
in TASK_19), which is largely *shared* across every token in a batch --
going from a k+1 flat batch to an N-node tree increases compute (more
attention/FFN FLOPs over more candidate tokens) without a proportional
increase in weight bytes read, so `T_verify_tree(N)` should grow
sub-linearly in N, not linearly. That is a reason to expect the ratio
above can come out favorable -- it is not a reason to skip measuring it.

**Go/no-go criteria, fixed before Phase 0 runs, not after**:
Phase 0 (cheap, offline, no distributed code) gives `E_tree(N)/E_linear(k)`
directly. Combine it with an *estimated* `T_verify_tree(N)/T_verify_linear(k)`
(reasoned from the sub-linear-batching argument above, using our own
measured linear verify costs from the Task 19 baseline bench as the
reference point) to get a projected `throughput_ratio` before writing any
tree infrastructure:

| projected throughput_ratio | decision |
|---|---|
| < 1.10 | Stop. Not worth the KV/rollback/protocol complexity in section 4. |
| 1.10 - 1.30 | Build the smallest possible Phase 1 prototype (depth 2, branching 2) *specifically* to replace the estimated `T_verify_tree` with a real measurement, then re-run this decision before committing to the full phase. |
| >= 1.30 | Proceed to full Phase 1. |

These exact numbers are starting points, not settled constants -- the
discipline that matters is picking thresholds before Phase 0's results are
in hand, so a disappointing result gets treated as an answer ("this
doesn't pay off, stop here") rather than a prompt to keep tuning until it
looks better.

## 1. What's already published, and what's actually reusable here

| Method | Core idea | Requires training? | Distributed across nodes? | Relevant to us |
|---|---|---|---|---|
| **Medusa** (Cai et al. 2024) | Extra prediction heads bolted onto the target model's final hidden state, one per future position; heads' top-k outputs combined into a fixed-shape tree; one forward pass verifies the whole tree via a **tree attention mask** (each candidate only attends to its own ancestors, not siblings) | Yes -- heads are fine-tuned per target model | No -- single GPU/node in the original design | **The tree attention mask trick is the key reusable idea.** The heads themselves are not reusable for us (we don't have a training pipeline and don't want to fine-tune a head per target model). |
| **EAGLE** (Li et al. 2024, EAGLE-1/2/3) | Small auxiliary transformer layer operating on the target's hidden states (not just embeddings), trained to predict feature-level future representations; EAGLE-2 makes tree shape dynamic/confidence-based instead of fixed | Yes -- auxiliary network needs training per target model | No | Higher acceptance than Medusa in published results, but the training requirement makes it the least practical of the three for us right now. Worth revisiting only if off-the-shelf approaches (below) prove insufficient. |
| **SpecInfer** (Miao et al. 2023) | **Multiple independent, off-the-shelf small models** propose candidates collaboratively, merged into one token tree, verified in a single batched pass with a tree attention mask (same masking trick as Medusa, applied to external models instead of trained heads) | No -- draft models are used as-is | Designed for multi-GPU single-server, but algorithmically model-agnostic | **This is the closest match to what we already do** (independent off-the-shelf draft models, no training step) and to what was proposed in this planning discussion (multiple weak nodes each running a different/independent draft, one fast node verifying the merged tree). Primary reference for Phase 2/3 below. |

Correction to an earlier hedge in this same discussion: an ensemble of
independent draft models feeding one verifier is not just a hypothesis --
SpecInfer already validates that design (in a single-server, multi-GPU
context). What's genuinely untested is whether it holds up (a) across our
network (draft sources on separate physical nodes, not local GPUs) and (b)
with the small, heterogeneous, none-too-strong draft models available to a
home cluster -- both open questions this plan needs to measure, not assume.

## 2. What we already have that this builds on

- Batched multi-token verify in one `llama_decode` call, `all_outputs=true`,
  reading `llama_get_logits_ith` per position (Task 19 bug #6 fix,
  `split_gen3_c.cpp::final_verify_wave`). Currently assumes a **linear**
  sequence of k+1 tokens. Tree verification is the same idea generalized to
  a branching structure with a masked attention pattern instead of a flat
  batch -- the batching and rollback infrastructure is the right starting
  point, not a rewrite from scratch.
- `fa_draft_buffer` / fa-link protocol ships **token ids only**, not hidden
  states (small payloads: `split_ab_send_verify_ids`). This is why adding
  more draft sources shouldn't blow up network cost the way tensor
  parallelism would -- confirmed by re-reading the actual wire format
  during this planning discussion, not assumed.
- `spec_confirmed` journal + KV rollback (Task 19 bug #5) already handles
  "some of what we speculatively computed turns out to be wrong, roll KV
  back to the confirmed position." A tree needs a stronger version of this
  (multiple divergent KV branches, not one linear tail to roll back), but
  the base mechanism -- and the lesson that rollback bugs are subtle and
  worth a dedicated journal -- carries over.
- draft_wait_estimator (level 1) and NORMAL/THROTTLED hysteresis (level 3)
  are orthogonal to this work and keep functioning underneath it -- they
  govern how long entry waits for A draft to arrive, regardless of whether
  that draft is a single line or a tree/ensemble merge.

## 3. Phased plan

### Phase 0 -- measurement harness (before writing tree code)
Before building tree verification, get a real number for "how much would a
tree even help" on our actual model pairs. Concretely: offline, replay a
handful of prompts through Llama-3.2-1B (or another draft) generating top-2
or top-3 candidates at each step instead of greedy top-1, and measure how
often the *target* model's actual next token appears somewhere in that
small candidate set vs. only in the single greedy pick. This is cheap
(no distributed code needed, can run locally) and gives `E_tree(N)` and
`E_linear(k)` directly.

Do not stop at the acceptance number. Per section 0.5, combine it with the
estimated verify-cost ratio and compute the projected `throughput_ratio`
before deciding anything -- apply the go/no-go table from 0.5, not a
standalone "did acceptance go up" judgment call.

### Phase 1 -- tree-structured verification (single draft model)
The actual hard infrastructure work, isolated from the multi-source
question so it can be validated on its own:
- Extend `final_verify_wave` from a flat k+1-token batch to a small
  fixed-shape tree (e.g. depth 2-3, branching factor 2) from **one** draft
  model, using a tree attention mask so siblings don't attend to each
  other.
- Extend KV rollback / `spec_confirmed` journal to handle a tree: after
  verification picks the accepted path, the *other* branches' KV entries
  need to be dropped, not just a linear tail.
- Reuse the existing fa-link wire format if possible (ship the tree as a
  small nested structure of token ids); avoid inventing a new transport
  unless the existing one genuinely can't express it.
- Bench against Task 19's linear baseline (same methodology as the
  2026-07-21 wait-policy bench: fixed prompt set, sequential non-overlapping
  sessions, averaged over multiple rounds) to get an honest tree-vs-linear
  number before adding ensemble complexity on top.

### Phase 2 -- multiple independent draft sources (SpecInfer-style ensemble)
Only after Phase 1's tree verification works and is measured:
- Run independent draft models on currently-idle pipeline nodes (e.g.
  node-a and node-b, which sit idle for most of a single-stream token's
  lifetime today) instead of colocating the sole draft with `final`.
- Merge their proposed continuations into one tree, verified in the same
  batched call from Phase 1.
- Validate the network-cost assumption directly: confirm per-candidate
  payload size stays small (token ids, as today) even with multiple
  independent sources shipping to the verifier concurrently, and re-check
  the draft_wait_estimator/THROTTLED behavior still makes sense with
  multiple inbound sources instead of one.
- This is also where the "verify-only fast node, draft-only weak nodes"
  role split from the planning discussion gets tested for real, using the
  network observability (`/network/stats`, Task 19 level 2) already built
  to see whether it's actually cheap in practice, not just in theory.

### Phase 3 (optional, only if 1-2 show real gains) -- trained auxiliary heads
Revisit EAGLE-style trained heads only if off-the-shelf tree/ensemble
methods hit a ceiling that a trained, target-aware head could plausibly
clear. Requires setting up a training pipeline this project doesn't
currently have -- explicitly out of scope until Phases 1-2 prove the
broader direction is worth that investment.

## 3.5 Code anchors (added 2026-07-22 cleanup)

Concrete entry points in this repo for each phase, so the work starts
from known code rather than a fresh search:

**Phase 0 (offline measurement)** -- no distributed code involved:
- New script `scripts/spec_topk_offline.py`. Simplest harness: run two
  local `llama-server` instances (binaries already built in
  `llama.cpp/build/bin/`) -- target = full Llama-3.2-3B GGUF (one-time
  ~2GB download; the cluster's layer-store copy is split and unusable
  here), draft = the 1B GGUF already cached on node-c under
  `models_dir/draft/`. Replay the 10 prompts from
  `docs/bench/2026-07-21_wait_policy/bench_prompts.txt` greedily through
  the target; at each position query the draft's `/completion` with
  `n_probs >= 3` and record whether the target's actual token is in the
  draft's top-1 / top-2 / top-3. Output: the `E_tree(N)/E_linear(k)`
  ratio for section 0.5's go/no-go table.

**Phase 1 (tree verification, single draft):**
- Verify kernel: `split_gen3_c.cpp::final_verify_wave` -- currently one
  `split_gen_decode_hidden(..., all_outputs=true)` over a LINEAR k+1
  batch, logits read per position via `llama_get_logits_ith`. Tree
  variant: represent branches as separate llama seq-ids sharing the
  prefix KV (`llama_memory_seq_cp` to fork a branch, existing
  `llama_memory_seq_rm` -- already used by our rollback -- to drop
  losing branches). This gets tree attention from llama.cpp's normal
  batch mechanics (per-token seq_id sets) without a custom mask kernel.
  Note `cparams.n_ubatch` is already 32 (Task 19 bug #6 fix) -- tree
  size must stay under it.
- Wire format: drafts currently ship as a flat id list
  (`split_ab_send_verify_ids`, cap `SPLIT_GEN_SPEC_MAX_K` in
  transport/runtime_protocol.h). A tree needs one extra parallel array:
  parent index per node (root = -1). Extend this message rather than
  inventing a new one -- entry's consumer block
  (`split_gen3_a.cpp::entry_process_work_item`, the
  `SPLIT_GEN_CMD_VERIFY && fa_buf` block) and final's
  `final_draft_and_ship` are the only two touchpoints.
- Rollback/journal: `spec_confirmed` journal and
  `split_gen_rollback_kv` in split_gen3_c.cpp assume a linear tail.
  With seq-id branches, the accepted path merges into seq 0
  (`llama_memory_seq_cp` back) and all other seqs are dropped -- the
  journal itself stays position-keyed and unchanged.
- Draft generation: `final_run_queued_session`'s draft_thread currently
  produces k greedy tokens per wave. Tree drafting = top-N sampling at
  chosen depths from the same draft ctx -- contained inside
  split_gen3_c.cpp, no protocol impact beyond the message above.

**Phase 2 (multi-source ensemble):**
- `fa_draft_buffer` (split_gen3_a.cpp) is single-slot by design
  ("final only ever has one outstanding draft"). Multi-source needs a
  keyed buffer (source id -> slot) plus a merge step before the wave is
  extended; `draft_wait_estimator` then needs a per-source or merged
  policy decision (open question flagged in the phase).
- Middle (split_gen3_b.cpp) has NO draft infrastructure today -- a
  drafting middle node would borrow split_gen3_c's draft-ctx loading
  (`--draft` arg plumbing in node_agent.cpp `start_worker` ~1813) and
  the orchestrator's `/draft/fetch` endpoint (node_agent.cpp ~2565)
  which is already node-agnostic.
- Orchestrator: fa_port allocation (`orchestrator.cpp` ~1792) currently
  wires exactly one final->entry link per session; N draft sources = N
  allocated ports, same pattern.

## 4. Open risks / things to validate early, not assume

- **KV memory cost of a tree.** Each branch needs its own KV slice while
  unresolved; on already memory-constrained nodes (see the Qwen2.5-32B
  install, which used most of a node's free VRAM) this could be tighter
  than it looks on paper. Check actual headroom before picking tree
  depth/branching factor.
- **Ensemble diversity is not guaranteed to help.** Directly demonstrated
  today: Qwen2.5-1.5B was a poor draft for Qwen2.5-32B (21% acceptance,
  worse than baseline). Three weak, poorly-matched draft models merged
  into a tree could just as easily be three correlated wrong guesses as
  three usefully diverse ones -- Phase 0's offline measurement should
  catch this before it's built into distributed infrastructure.
- **Rollback correctness.** Task 19's KV rollback bugs (the permanent-
  draft-death issues, bug #5) were subtle even for a single linear draft
  path. A tree with multiple simultaneously-live branches is a strictly
  harder version of the same problem -- budget real time for this, not
  just the happy path.
- **Verify-side compute cost.** This is not just a risk on this list -- it
  is the project's central hypothesis, see section 0.5. Restated briefly
  here as a reminder: a tree verify call does more total work than a
  linear k+1 batch, and it is entirely possible for acceptance to go up
  while throughput goes down if verify cost grows faster than acceptance
  does. Do not evaluate Phase 0/1 results on acceptance alone.

## 5. Explicit non-goals for this task

- Not doing tensor-parallel / sub-layer splitting (see section 0).
- Not building a training pipeline for EAGLE-style heads unless Phase 3 is
  reached.
- Not touching the Task 19 linear speculative mechanism's defaults
  (p80 wait policy, THROTTLED hysteresis) -- this sits alongside it, not
  instead of it.
