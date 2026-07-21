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

## G. Phase 3 cluster bring-up log (2026-07-19)

First real-cluster speculative runs (smollm2-1.7b target / SmolLM2-360M
draft, k=4, layout entry=node-b M1, middle=node-a M3, final=node-c 4070Ti).
Getting from "works locally" to "works on the cluster" surfaced six real
bugs, each found via the SPEC_DEBUG acceptance instrumentation:

1. `e6c7b4fbf` -- missing `<algorithm>` include broke the GCC build.
2. `706f379e3` -- Windows worker stdout/stderr silently discarded
   (bInheritHandles=FALSE, no console); every Windows worker crash ever
   had been invisible. Workers now log to models_dir/logs/worker_*.log,
   readable via /debug/log?worker=role.
3. `14f354f2c` -- draft load called split_gen_load_model, which in
   layer-store mode ignores its path argument and reloaded the PRIMARY
   model a second time on the same GPU (crash with no error output).
4. `49e4528a6` -- the draft raced the token's own return path and lost by
   ~1-2 ms most waves (hit,hit,...,miss forever pattern); entry now waits
   up to 8 ms on a cv for the fa shipment, and final's draft compute
   moved off the consumer thread (it was delaying every next wave).
5. `e9ecbdf7b` -- two permanent-draft-death bugs: any KV gap killed the
   draft forever (fixed with a confirmed-token journal that replays the
   missing span), and RESET raced the prime over the slower bc path and
   wiped it (RESET no longer touches draft state; the prime is
   self-contained).
6. `752dacaa4` + `6849b3225` -- the Phase 1 "sequential verify" limit was
   self-imposed: hidden injection is ubatch-agnostic, so verify waves now
   decode as ONE graph on final (n_ubatch 1->32, logits at all
   positions; rejected KV tail handled by the existing rollback rule)
   and on middle (waves up to 32 tokens).

Measured (64-token runs, same prompt, entry_queue=0 both sides):
baseline 22.5-27.3, speculative 40.8-45.7 tok/s -- **x1.64 median**,
acceptance 2.0/wave (E=3.0 tok/wave), streams bit-identical to baseline.
Later same-evening runs showed Wi-Fi variance exceeding the
speculative-vs-baseline gap (baseline itself swung 22-34), so the x2
gate needs a calmer network window and/or the remaining levers:
- entry still decodes k+1 tokens as k+1 single-token graphs
  (n_ubatch=1) and pays up to 8 ms cv-wait on a draft miss;
- SmolLM2 pair acceptance is only ~50% at k=4; the validated Llama-3.2
  3B/1B pair (85-90%) would lift E from ~3.0 to ~3.7-4.2;
- adaptive-k / kill-switch (F.1) unimplemented -- misses still cost a
  full verify wave.

Phase 3 status: mechanism fully working end-to-end on the real cluster
(acceptance, determinism, resident draft cache, all races fixed);
benchmark gate pending a clean measurement window.

Revised 2026-07-18 (implementation, `27a38b59c` then simplified in
`190f5c604`): "must not block on draft readiness" from the original note
above was wrong. Implemented as parallel-but-blocking instead:
`setup_runtime_graph` kicks off the draft fetch right after the final-role
node is known, alongside the primary model's own per-stage prepare loop,
then joins it before the configure phase -- even if the primary model's
layers were already cached and that loop finished instantly. A session
that asked for `speculative_draft_model_url` gets it or gets a clear
degraded-to-no-speculation log line, never a silent skip because the main
model happened to be warm.

Also revised: no model registry involvement at all. The draft is a fixed
model at a known URL, not something that needs manifest discovery or
layer-range slicing -- `POST /draft/fetch {source_url, filename}` on the
final node just does a plain `curl -o` (`dist_http_download_file`) into
`models_dir/draft/`. That endpoint also skips the download outright when
the destination file already exists, which resolves (1) -- the
cross-session resident cache -- as a side effect: the draft simply stays
on disk across sessions as long as the node keeps holding `final`, no
extra caching logic needed. The one remaining gap is the draft
llama_context itself: split_gen3_c currently reloads and re-creates it
per process lifetime, not shared across separate final-worker process
restarts on the same node -- fine for now since a worker process already
lives for the session, not per-request.

## H. Entry wait-window: adaptive policy + baseline bench (2026-07-21)

Motivation: the fixed 8 ms bounded wait for a draft (bug #4 fix above)
turned out to still be a fixed constant on a Wi-Fi link (node-b) whose
RTT swings roughly 5-100+ ms within seconds. A burst window produced
100% draft_miss for several seconds straight -- confirmed live (`ping -i
0.2` to node-b showed a ~4s run of 7-140ms hops mid-series) -- which is
what motivated making the wait adaptive instead of constant.

**Mechanism** (`split_gen3_a.cpp`, commit `bcfb1468b`): `fa_draft_buffer`
gained `draft_wait_estimator`, a 128-sample ring buffer of draft arrival
latency (hit = actual wait time, miss = censored at the window used, i.e.
"at least this long"). Recomputes `wait_window_ms = clamp(percentile +
2ms margin, 4ms, 30ms)` every 32 samples (not every one, to avoid the
window itself oscillating). Logs `wait_window/arrival_p50/arrival_p95/
hit_rate` on every recompute.

**Policy is swappable without a rebuild** (commit `f53f60983`) via
`SPEC_WAIT_POLICY` on the entry node: `fixed:<ms>` or `p50`/`p80`/`p90`/
`p95`/`p99` (default, matches the original hardcoded behavior).

**Network observability** (`node_agent.cpp`, commit `843fe2ff4`): every
node now backgrounds a 1/s ping of its peers' existing `/health`
endpoint (peer list refreshed from the orchestrator's `/nodes` every
~5s), tracked in a 128-sample ring per peer and exposed as
`GET /network/stats` -> `{peers: {node_id: {rtt_p50_ms, rtt_p95_ms,
rtt_p99_ms, jitter_ms, loss_pct, samples}}}`. No new wire protocol.
Built specifically so a bench run can record the network state it
actually ran under instead of assuming it was constant.

**Baseline bench methodology**: llama-3.2-3b target / llama-3.2-1b
draft, k=4, layout entry=node-b (Wi-Fi), middle=node-a, final=node-c.
10 fixed prompts (prose/code/qa/email/history-ish mix, `max_tokens=64`
each) run sequentially on one non-overlapping session per policy (never
two concurrent sessions -- see the session-eviction methodology bug
earlier in this doc), destroyed before the next policy's session is
created. Full raw data, bench script, and prompt set:
`docs/bench/2026-07-21_wait_policy/`.

Results (2 replicated rounds; round 2 also has node-b's `/network/stats`
view of its link to node-c, i.e. the RTT the draft actually had to
cross):

| policy | round | avg tok/s | hit_rate | net RTT p95 (node-b to node-c) |
|---|---|---|---|---|
| fixed:8  | 1 | 38.55 | 70.1% | not recorded |
| fixed:8  | 2 | 36.48 | 72.0% | 18.65ms |
| fixed:16 | 1 | 40.29 | 95.9% | not recorded |
| fixed:16 | 2 | 45.81 | 97.0% | 13.11ms |
| p80      | 1 | 43.04 | 85.2% | not recorded |
| p80      | 2 | 44.53 | 82.8% | 16.01ms |
| p95      | 1 | 33.52 | 85.5% | not recorded |
| p95      | 2 | 40.20 | 87.2% | 14.68ms |

Findings:
- Higher hit_rate does not imply higher throughput: `fixed:16` at 96-97%
  hit_rate only modestly beats `p80` at 83-85%, and in round 1 `p95`
  (85.5% hit) was the slowest of all four despite a hit_rate on par with
  `p80`. Speculative decoding here is a total-cost optimization
  (wait cost vs. acceptance payoff), not an acceptance-maximization
  problem.
- `p80` beat `p95` in both rounds, and in round 2 did so *despite* running
  under a worse-measured network (RTT p95 16.01ms vs 14.68ms) -- the one
  comparison in this dataset that survives the network confound rather
  than being explained by it.
- `fixed:16` vs `fixed:8` and `fixed:16` vs `p80` are directionally
  consistent across rounds but each time also came with a better-network
  round for the winner, so those specific orderings are not yet
  separated from network variance -- round 2's network wasn't constant
  across the four policy runs (RTT p95 ranged 13.1-18.7ms across a
  ~15-minute span), confirming the network itself needs to be logged
  every time, not assumed constant.
- Working conclusion: `p80` is the best-supported policy of the four
  tested, adopted as current best-known (not declared final). `fixed:8`
  (the original hardcoded behavior) is confirmed suboptimal.

This table is the intended baseline for future comparison, not a final
answer -- e.g. "throughput went from ~43 tok/s to N tok/s after the
planner became network-aware" once that work (network-aware role
placement, dynamic wave size) exists. Keep it even after a better policy
is found.
