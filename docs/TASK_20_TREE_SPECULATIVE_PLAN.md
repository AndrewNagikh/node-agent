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
(no distributed code needed, can run locally) and tells us whether tree
branching is likely to move acceptance meaningfully before investing in the
verification-side engineering. Skip Phase 1 if this doesn't show a real gap.

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
- **Verify-side compute cost.** A tree verify call does more total work
  than a linear k+1 batch (more candidate tokens per wave). Need to check
  this doesn't push the "final" node's per-wave compute time past the
  point where it stops being worth the extra acceptance -- same
  cost-vs-payoff framing as the wait-window work, applied to tree size
  instead of wait time.

## 5. Explicit non-goals for this task

- Not doing tensor-parallel / sub-layer splitting (see section 0).
- Not building a training pipeline for EAGLE-style heads unless Phase 3 is
  reached.
- Not touching the Task 19 linear speculative mechanism's defaults
  (p80 wait policy, THROTTLED hysteresis) -- this sits alongside it, not
  instead of it.
