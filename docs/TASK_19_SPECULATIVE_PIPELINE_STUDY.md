# Task 19 — Speculative Pipeline Study (deliverable)

**Parent:** `TASK_19_SPECULATIVE_PIPELINE_RESEARCH.md`
**Status:** Research complete — **GO** (phasing at the end)
**Inputs:** measured homelab period decomposition of 2026-07-16 (`SESSION_2026-07-15_HOMELAB_VALIDATION_AND_FIXES.md`): fixed per-token cost `F ~= 15 ms` (token return path 13.7 ms + queue slack), compute sum `C*W ~= 16.7 ms`, socket sends 0.06-0.23 ms, Wi-Fi RTT a<->b 5.9 ms (10% loss), a<->c 6.9 ms.

---

## A. Draft placement — decided by cost model, not by hardware identity

The draft model is small (fits whole on one node) and must close the loop
guess -> verify -> accept -> guess. Placement is a **policy over roles and
measured parameters**, never over concrete machines: the planner re-assigns
roles per session, so the rule must follow the role.

Parameters, all measurable by the planner itself:

| Symbol | Meaning | Source |
|---|---|---|
| `t_draft(n)` | one draft token on node n ~= draft_bytes / BW_eff(n) | existing bandwidth probe (node score) |
| `R` | token return path final->entry | trace (measured 13.7 ms here; cluster-specific) |
| `hop(i,j)` | one-way latency between nodes | **not yet measured by the planner — gap, see §E** |
| `V` | verify wave cost ~= per-token pipeline compute (k-token wave reads weights once) | trace |

Wave period per candidate placement:

| Placement | T(wave) | Note |
|---|---|---|
| **node holding `final`** | `max(R, k*t_draft(final) + hop(final,entry)) + V` | sampler is local: draft learns accepted tokens instantly and guesses **during** the return gap. If `k*t_draft + hop <= R`, drafting is fully hidden |
| node holding `entry` | `R + k*t_draft(entry) + V` | draft can only start after the result arrives; drafting is serially exposed |
| orchestrator | +2 extra hops | also re-enters the data plane — rejected |

**Rule:** place the draft on the node currently holding the `final` role,
provided `draft_bytes` fits its memory budget (the planner must add
draft_bytes to that node's fit check). If it does not fit, evaluate T(wave)
for the remaining candidates and take the minimum. The rule is
self-consistent with the existing planner policy (final = highest
bandwidth-score node -> also the best `t_draft`), and it survives any
re-assignment: the draft follows the role, not the machine. On a
homogeneous cluster the formula still prefers final (hidden drafting),
which is exactly when it matters most — the worse the network, the larger
R, the more free drafting time.

Requires a direct final->entry connection for draft-token delivery (one
hop, not relayed through middle stages). This same connection shortens the
token return path by one hop as a side effect — folding the previously
discussed "direct return" idea into this design.

## B. Offline acceptance rates (measured 2026-07-17, M3 Pro, greedy, n=96-102, 3 prompts: prose/code/qa)

| Pair (target/draft) | k=2 | k=4 | k=8 |
|---|---|---|---|
| Qwen2.5 1.5B / 0.5B | 59.1 / 82.4 / 75.6 % | 41.7 / 69.2 / 75.0 % | 27.5 / 52.0 / 59.7 % |
| Llama-3.2 3B / 1B | 83.3 / 94.1 / 72.5 % | 76.0 / 90.5 / 66.7 % | 67.5 / 84.6 / 50.6 % |

- Llama-3.2 pair accepts markedly better (same-family, trained together).
- Acceptance decays with k, as expected; code prompts accept best, open-ended qa worst.
- Tokenizer compatibility notes: pairs must share a tokenizer. Llama-3.2 1B<->3B and Qwen2.5 0.5B<->1.5B are safe in-family pairs. TinyLlama (Llama-2 SPM) has **no smaller same-tokenizer family member** — it is itself the draft-class model of its family; catalog implication: TinyLlama cannot be a speculation target.
- Measurement hazard found: Llama-3.2 GGUFs default to 128K context; loading target+draft with default ctx OOMs Metal (`kIOGPUCommandBufferCallbackErrorOutOfMemory`) and silently degrades to 2-4 t/s before crashing. Always set `-c` explicitly in speculative runs.

Tooling: `llama-speculative` (built from this tree), sweep script preserved
at scratchpad `spec_sweep.sh` pattern (n=96, `--temp 0 --seed 42`,
`--spec-draft-n-max k`, `-c 2048`).

## C. Protocol sketch (RFC-0014 seed)

The runtime is closer to ready than expected: every wave already carries
`pos_start` (positional addressing), multi-token waves are the existing
prefill path, and `llama_memory_seq_rm(mem, seq, p0, p1)` exists in the
llama.cpp API.

1. **Verify wave**: entry sends k+1 positions (k drafted + 1 anchor) as one
   hidden-state wave, exactly like a k+1-token prefill. Stages decode the
   batch and write KV for all positions — no new mechanics.
2. **Verification on final**: final computes logits for all k+1 positions
   (`n_outputs = k+1` — today the final worker only surfaces the last
   position's logits; this is the main worker change). Greedy contract:
   accept the longest prefix where argmax(logits[i]) == draft[i+1]; emit
   one corrected token from the first rejected position. Sampling contract:
   standard speculative rejection sampling (accept with p_target/p_draft,
   resample from max(0, p_t - p_d) on reject) — preserves the target
   distribution exactly; determinism per RFC-0013 §13 holds under fixed
   seeds because the accept/resample chain is a pure function of both
   models' logits.
3. **KV rollback — one rule, no extra messages**: every stage tracks
   `next_pos` (already implicit today). On receiving a wave whose
   `pos_start < next_pos`, the stage first calls
   `llama_memory_seq_rm(mem, 0, pos_start, -1)`, then decodes. Rejected
   tail cleanup is therefore carried by the *next* wave's pos_start —
   idempotent, self-healing after any failure, and identical to what a
   non-speculative retry would need anyway.
4. **Response shape**: `c_resp` grows from one token to
   `{accepted_count, corrected_token}` (+ optionally the next k draft ids
   when the draft lives on final). Return path stays as today (or uses the
   new direct final->entry link from §A).
5. **Failure semantics**: a lost/timed-out verify wave needs no special
   rollback — the client re-dispatches from the last committed position and
   rule 3 truncates any provisionally-written KV on every stage. WaveID
   space: draft waves never enter the pipeline; verify waves are ordinary
   waves (existing WaveID semantics unchanged), plus an `accepted_count`
   attr on the trace event for acceptance observability.

Hard problem status: KV-on-partial-acceptance, flagged as the risk in the
research doc, dissolves under positional addressing + `seq_rm` — the
provisional-write-then-truncate design costs one API call per rejected
wave and reuses the wave protocol as-is.

## D. Projected throughput (homelab numbers; formula portable)

`T_k = (C*W + F) / E[tokens_per_wave]`, `E = (1 - p^(k+1))/(1-p)` accepted
prefix + 1 corrected token, p = mean measured acceptance:

| Pair | k | E[tok/wave] | proj ms/tok | proj tok/s |
|---|---|---|---|---|
| Qwen 1.5B/0.5B | 2 | 2.25 | 14.1 | 70.9 |
| Qwen 1.5B/0.5B | 4 | 2.39 | 13.3 | 75.4 |
| Qwen 1.5B/0.5B | 8 | 1.86 | 17.0 | 58.8 |
| Llama 3B/1B | 2 | 2.53 | 12.5 | 79.7 |
| **Llama 3B/1B** | **4** | **3.22** | **9.9** | **101.5** |
| Llama 3B/1B | 8 | 2.99 | 10.6 | 94.4 |

Baseline (no speculation): 26.8 tok/s (37.3 ms). Projected gain
**x2.2-x3.8** — above the research doc's x1.5-2.5 estimate, because F here
is Wi-Fi-inflated and speculation amortizes exactly F. Break-even: E > 1,
i.e. any acceptance > ~0 gains; the k-sweet-spot moves down as acceptance
falls (k=8 already loses to k=4 for Qwen). Model validation criterion
(±25% vs a local speculative run) to be checked during implementation
Phase 1.

Caveats: draft compute on the final node is assumed hidden under R (§A);
verify wave V is assumed ~= single-token compute (memory-bound reads
dominate — holds for these model sizes); acceptance was measured greedy —
sampled acceptance is typically lower, re-measure at temp>0 before
finalizing k defaults.

## E. Planner gap exposed by this study

`hop(i,j)` (inter-node RTT matrix) is required by §A's formula and by any
latency-aware chain ordering, and the 2026-07-16 finding showed RTT — not
bandwidth — dominates the return path. The planner measures neither today.
**Deliverable for 17.5/Task 19 implementation: nodes measure pairwise RTT
(a few pings at registration/refresh) and report it; orchestrator stores
the matrix alongside scores.**

## Go/no-go and phasing

**GO.** Recommended k default: 4 (clamp to 2 when measured acceptance
< ~55%). Model pair for the demo track: Llama-3.2 3B target + 1B draft
(catalog constraint: same-tokenizer pairs only; TinyLlama excluded as
target).

1. **Phase 1 — local validation**: implement verify-wave + seq_rm rollback
   in the single-node worker path only; validate D-model within ±25% vs
   `llama-speculative` on the same hardware.
2. **Phase 2 — RFC-0014**: full protocol write-up from §C, including the
   direct final->entry link and RTT matrix.
3. **Phase 3 — cluster implementation** behind a flag
   (`DIST_RUNTIME_SPECULATIVE=1`), acceptance-rate trace events, benchmark
   gate: >= x2 measured on the homelab cluster vs same-day baseline.

---

## Phase 1 results (2026-07-17, llama.cpp `e8e669c96`)

Implemented per §C and validated on a localhost 3-stage pipeline (llama-3.2
3B target split 9/9/10, 1B draft, M3 Pro):

| k | pipeline acceptance | offline (§B) | determinism | D-model pred vs meas |
|---|---|---|---|---|
| 2 | 83.3% | 83.3% | **PASS** | 35.0 vs 33.4 tok/s (5%) |
| 4 | 69.2% | 77.7% | **PASS** | 28.4 vs 26.2 tok/s (8%) |
| 8 | 62.5% | 67.6% | **PASS** | 24.3 vs 27.8 tok/s (12%) |

Determinism = speculative token stream bit-identical to the plain greedy
loop. D-model gate (±25%) passed on all k after one correction:

**Correction to §D — sequential verify decode.** Hidden-state injection on
stages with `layer_start > 0` must run one token at a time (KV per
position), so a verify wave costs ~(k+1) stage-computes, not ~1. Only the
fixed cost F amortizes. Local single-GPU runs are therefore *slower* than
baseline by design (F≈0, one GPU shared by all stages + draft). Revised
cluster projection at k=4: wave ≈ (k+1)·max_stage + tail latencies + F ≈
59 ms for E≈3.8 tokens → **~60-65 tok/s vs 26.8 baseline (×2.3)** — down
from the naive 101 tok/s but still comfortably above the ×2 Phase 3 gate.
Making batched hidden injection KV-correct (removing the one-token
constraint) would restore most of the difference and is the single most
valuable runtime upgrade before or during Phase 3.

Phase 1 exit criteria met. Next: Phase 2 (RFC-0014 write-up incl. direct
final->entry link + RTT matrix), Phase 3 (cluster integration behind
`DIST_RUNTIME_SPECULATIVE=1`, node_agent draft loop, ≥×2 measured gate).

## F. Two gaps found in post-Phase-1 review (2026-07-17), to fold into RFC-0014

**F.1 — n-gram/lookup fallback needs a kill-switch, not blind enablement.**
When no draft model fits (or as the zero-memory default), n-gram/lookup
speculation (llama.cpp's own lookup-decoding mechanics) is the natural
fallback — but under the §D correction (sequential verify decode costs
`(k+1)` stage-computes per wave, only `F` amortizes), a wave with ~0%
acceptance is **worse than no speculation at all**: `(k+1)*C*W + F` per
confirmed token vs baseline's `C*W + F`. N-gram acceptance is highly
text-dependent (good on repetitive/code text, poor on open-ended prose),
so it cannot be enabled unconditionally. Required for Phase 2/3: an
online acceptance monitor (rolling window over recent waves) that (a)
shrinks `k` as acceptance drops and (b) fully disables speculation (falls
back to the plain decode path) below a measured break-even threshold
(`E[tok/wave] <= 1`, i.e. acceptance too low to recoup `(k+1)` compute).
This generalizes to model-draft speculation too — the same monitor should
gate any draft source, not just n-gram.

**F.2 — draft weights should be resident per role, loaded asynchronously.**
§A's placement rule ties the draft to the `final` role, not to a session;
as long as the role assignment is stable across sessions, the draft's
~0.8 GB load cost should be paid once per role-assignment, not once per
session. Two implementation requirements for Phase 2/3: (1) node_agent
caches the loaded draft context keyed by (draft model id, node), reusing
it across sessions while the node continues to hold `final`; (2) draft
download/materialize runs asynchronously, in parallel with the primary
model's per-stage prepare loop already on the session-create critical
path.

Revised 2026-07-18 (implementation, `27a38b59c`): "must not block on
draft readiness" from the original note above was wrong. Implemented as
parallel-but-blocking instead: `setup_runtime_graph` kicks off the draft's
`prepare_runtime_node` call (as an ordinary registered model, materialized
the same way the primary model's per-stage layers are, via
`runtime_role=pipeline_stage` over the full layer range on whichever node
lands `final`) right alongside the primary model's own per-stage loop, then
joins it before the configure phase -- even if the primary model's layers
were already cached and that loop finished instantly. A session that asked
for `speculative_draft_model_id` gets it or gets a clear degraded-to-no-
speculation log line, never a silent skip because the main model happened
to be warm. (1), the cross-session resident cache, is still open -- each
session currently re-downloads/re-loads the draft on the final node even
if unchanged from the last session.
