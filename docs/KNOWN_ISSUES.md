# Known Issues

Running log of bugs found during manual/dashboard testing that aren't
tied to a specific gate report. Newest on top. Each entry: what broke,
how it was found, root cause (if known), status.

---

## OPEN

### The orchestrator acts on cluster state it has not verified
**This is the root cause behind most of the incidents below**, recorded
separately because the individual fixes treated symptoms.

`trigger_cluster_optimization_async` runs on node registration. After a
restart the first node back triggers it while the others are still
booting, so their layers look absent, every model looks broken, and the
optimizer "repairs" it by moving layers onto whatever is up -- deleting
the copies elsewhere. Coverage polled while a node is merely busy loading
tensors produces the same false picture.

Mitigations landed 2026-07-24 (`74371b6`, `c7d2296`): re-poll before
judging, skip models whose layout names an offline node, cap futile
relayouts, refuse one-stage pipelines, self-heal a structurally broken
layout at session create. Those stopped the data loss. They do not make
the design correct -- the layout is still derived from a live view while
installed blobs are expected to follow it.

**Full fix is Task 24** (`TASK_24_CLUSTER_STABILITY.md`): invert the
authority so installed layers are the durable state. The decision itself
is now an explicit, unit-tested pure function (`layout_policy.h`,
`b8ad88d`) but is **not yet wired into the orchestrator** -- that changes
a destructive path and wants the cluster available to verify.

### Verify waves are not batched: ~(k+1) stage-computes per wave
Hidden-state injection on stages with `layer_start > 0` runs one token at
a time, because KV is written per position. So verifying a k-token wave
costs about `k+1` stage-computes instead of the ~1 that batching would
give; only the fixed per-wave cost `F` (network hops, dispatch) is
amortized. Measured and written up in `TASK_19_SPECULATIVE_PIPELINE_STUDY.md`
§D, which already called the fix "the single most valuable runtime
upgrade."

Not a defect -- results are correct -- but it is the ceiling on
speculative decoding here, and it is easy to forget when reasoning about
cost, because the natural assumption is that a wave batches like prefill
does. It has now cost us twice:

- It is why speculation needs a *high* acceptance rate to pay off at all.
  At 19% (the 70B + 1B pair below) a wave produces ~1.23 tokens for 5
  stage-computes, which is why that pair showed no speedup.
- It closed Task 20 (tree speculation) outright on 2026-07-28. That plan
  assumed verify cost grows sub-linearly in candidate count; under the
  real sequential behaviour a width-2 tree projects to 0.21x throughput
  against a 1.10 stop-threshold. See
  [PHASE0_REPORT.md](bench/2026-07-27_tree_spec_phase0/PHASE0_REPORT.md).

Fixing it (KV-correct batched hidden injection) would speed up the linear
speculation already in production and is the prerequisite for reopening
Task 20 -- in that order of importance.

### Coverage is reported stale and read as breakage
Coverage is computed at whatever moment it happens to run and then
served from the registry without any indication of when it was taken. On
2026-07-27 eleven of fourteen models showed DEGRADED with partial layer
counts immediately after a restart; a forced refresh returned all but
one to READY. Nothing had been lost.

Same day, the same symptom had a different cause: node-a was simply not
running, while `/nodes` still listed it online from a stale registration.
Eight models "recovered" the moment it was started, with no repair at
all.

Both times the honest answer was "the report is old", and both times it
was initially read as damage. A freshness timestamp on the coverage
record, and a UI that shows it, would have made the difference obvious.

---

## DESIGN REVERSALS (logged so the reasoning isn't lost)

### RTT-aware entry placement (Task 21.1 / G4) removed
**Symptom that exposed it:** recurring `runtime coverage not ready` on
session-create, and models sitting in `DEGRADED` coverage despite having
every layer installed. On 2026-07-24 five of fourteen models were
DEGRADED at once (`llama-3.2-1b`, `llama-3.2-3b`, `phi-3.5-mini`,
`qwen3-14b` -- pure desync, install-plans were 100% DELETE with 0 bytes
to download; `gemma-3-1b` also had one genuinely missing layer).
**Root cause:** the planner chose the entry node by measured p95 RTT to
final. Home-network RTT is noisy and non-stationary, so entry moved
between sessions with no durable reason, and each move orphaned that
model's installed layers on the previously-chosen node.
**Decision (2026-07-24, `65ed1f60a`):** reverted to score-order entry
placement. The latency win was never actually measured -- the A/B in
`TASK_21_PROVEN_PRACTICES_PLAN.md` Item 1 was specified but never run --
while the layer-churn cost was real and repeated. Moving layer placement
is expensive; keeping it stable is free. `/network/stats` is untouched
and still available for observability.
**Follow-up (2026-07-24, `dd35417ff`):** removing RTT left score as the
one remaining input that could still reorder roles on noise -- it comes
from live measurements and drifts under unrelated load. Added relayout
hysteresis: the previous role ordering is kept unless a node's score
beats the incumbent's by >10% (2x the 5% delta `/register` uses to
notice a change at all). Real topology changes still bypass it, and it
can never put a GPU node behind a CPU-only one. Covered by
`test-layout-hysteresis`, which asserts repeated jitter is a fixed
point.
**Extended (2026-07-24, `d274f4c8e`):** Task 21.4 made layer *counts*
follow measured `decode_tps`, which is a live measurement with the same
drift problem -- so counts got their own hysteresis at 20% predicted
single-stream gain (Petals' rebalancing threshold, arXiv:2312.08361),
higher than the 10% used for role ordering because a count change moves
weights rather than just relabelling a stage. **Not yet verified on the
cluster** (unavailable when this landed): needs the tok/s A/B on 3B and
32B that `TASK_21_PROVEN_PRACTICES_PLAN.md` Item 4 specifies, so this
does not repeat G4's mistake of shipping an unmeasured win.

---

## DISMISSED

### Decode speed anomaly: 2.1 tok/s on llama-3.2-3b (expected ~16-29)
**Found:** 2026-07-24. **Dismissed:** 2026-07-24, same day -- user
confirmed it was a one-off, not reproduced further. Live checks at the
time (no lingering processes, normal RTT to all nodes) already pointed
this way; not chasing further unless it recurs. If it comes back,
re-open with a fresh symptom description rather than reusing this entry.

---

## NOT A BUG (logged so it isn't re-investigated from scratch)

### 70B target + 1B draft: speculative decoding gives no speedup (19% hit rate)
**Found:** 2026-07-24, dashboard testing, llama-3.3-70b-q3ks +
Llama-3.2-1B-Instruct draft, k=4.
**Result:** 2.2 tok/s, same as the non-speculative 2.28 tok/s baseline.
Root cause confirmed via entry node's `SPEC_DEBUG` log: `hit_rate=19%`
-- the draft and target simply don't agree often enough for this pair,
despite being the "same family." Not a code defect; the pipeline
verified/rejected correctly the whole time. Full writeup:
[L3_70B_REPORT.md](bench/2026-07-24_l3_70b/L3_70B_REPORT.md#speculative-decoding-attempt-no-speedup-with-the-1b-draft).

---

## FIXED

### KV cache was allocated for the whole model on every node
**Found:** 2026-07-27, while investigating whether a distributed KV cache
was worth building. It turned out one already should have existed.
**Symptom:** context could not be raised; memory accounting never matched
what nodes actually used.
**Root cause:** `llama_kv_cache` sizes itself from `hparams.n_layer_all`,
and the only per-layer gate is a `filter` callback that was null on the
normal path. `cparams.layer_start/layer_end` -- the range that decides
which layers a node *executes* -- never reached it. So a node holding 28
of 80 layers reserved cache for all 80, and every node in the cluster
held a full copy. Aggregate memory was never the limit; replication was.
**Fix (`293d5e661`):** compose a range filter with the existing
architecture filters, placed before the SWA branch so it covers all three
construction paths. The first attempt patched only the plain path and a
Gemma-3 model went straight past it through `llama_kv_cache_iswa` --
worth remembering, that path is easy to miss.
**Measured:** Gemma-3-1B, 26 layers, n_ctx 4096 -- 104 MiB full range vs
32 MiB for layers [0,8). Live on the cluster: 5/5/18 layers cached
instead of 28 on each node, 448 MiB total against ~1.3 GB before.
Also fixes an accounting mismatch: `layout_planner` already charged KV
per assigned layer, so its fit check had been optimistic.

### Generation capped at ~500 tokens regardless of the model
**Found:** 2026-07-27, reported as `decode token ready failed at step 493`
-- an error naming neither the context nor the real limit, and pipeline
recovery then hit the same wall and reported it twice.
**Root cause:** every worker built its context with a hardcoded
`cparams.n_ctx = 512`, while the planner had been sizing KV for the
session's 4096. Memory was reserved for one context and the workers ran
another.
**Fix (`46e1bda`):** the session's `n_ctx` now flows create -> configure
-> worker `--ctx-size`, across all three pipeline stages and the per-node
embedding/output services (stages must agree; the one with the smaller
cache would be the one to fail mid-sequence). Clamped to the model's own
trained context, and the effective/native/requested values are returned
from `/session/create` so a caller can size `max_tokens` instead of
discovering the limit by hitting it.
**Verified live:** 700 tokens generated where 493 used to fail.

### Distributed generate loop never checks for EOG (end-of-turn) tokens
**Found:** 2026-07-24, dashboard testing after wiring up chat-template mode.
**Symptom:** Model answers correctly, then keeps going past its natural
end -- output contains literal special tokens (`<|eot_id|>`,
`<|start_header_id|>assistant<|end_header_id|>`) and hallucinated
follow-up turns, instead of stopping.
**Root cause:** Checked the whole distributed pipeline
(`orchestrator.cpp`, `node_agent.cpp`, `split_gen3_a/b/c.cpp`) for
`llama_vocab_is_eog()` -- it was called in exactly one place in the
whole tree, `e2e_common.h:843`, a local single-process test harness,
**not** the `/session/generate` production path. The real generate
loop always ran exactly `max_tokens` steps regardless of whether the
model produced an end-of-turn token.
**Fix, part 1 (2026-07-24, `9a0e77acd`):** `run_local_pipeline_generate`
(`node_agent.cpp`) truncates `out_tokens` at the first
`llama_vocab_is_eog()` token, using the same `tokenizer_service_vocab()`
already loaded for chat-template application, right before returning to
the orchestrator. Fixes the client-visible symptom completely.
**Fix, part 2 (2026-07-24, `5fa62c19f`):** initially flagged the wasted
middle/final compute as needing a wire-protocol version bump (deferred
as task #38) -- turned out unnecessary. Entry already receives the
sampled `token_id` on every response, so it can check
`llama_vocab_is_eog()` itself and simply stop sending further
`SPLIT_GEN_CMD_DECODE`/`VERIFY` requests, with no new field on the wire
structs. Covers all three decode paths (synchronous, entry_queue +
client_pipeline look-ahead, speculative wave loop); reuses the existing
drain-pending path for the look-ahead case. `truncate_at_eog()` from
part 1 stays as a correctness backstop, not the saving mechanism.
**Verified:** both parts compile clean on node-a; **not yet
live-verified** through the dashboard (needs node-a/b/c + orchestrator
all rebuilt and restarted, same deploy story as the chat-template fix).
Confirm with the same "привет" prompt next session before closing this
out for real -- also worth confirming decode actually stops early
(fewer network round-trips / faster wall-clock on a short reply) not
just that the text looks right.
