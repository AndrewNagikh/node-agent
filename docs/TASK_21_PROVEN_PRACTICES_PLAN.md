# Task 21 -- Adopting proven practices from the prior-art survey (plan)

Status: PLANNING, not started. Written 2026-07-22, directly derived from
docs/research/2026-07-22_distributed_inference_survey/ (see SURVEY.md).
Scope: practices that are already validated in production/published
systems -- above all Petals -- and that map onto measured bottlenecks in
this cluster. This is adaptation work, not research: every item below
cites the system that already proved it.

Relationship to Task 20 (tree speculative): independent and mostly
cheaper. Items 1-2 reduce per-token network latency for EVERYTHING
(plain decode, linear speculation, and any future tree speculation), so
they should land before Task 20's heavy engineering -- a faster network
path raises the payoff of every speculative mechanism built on top.

## Current state these items attack (measured, not assumed)

- Hidden states cross the wire as raw fp32: `split_tcp_send_hidden(fd,
  n_tokens, n_embd, layer_end, const float * data)` in
  transport/split_tcp_wire.h. For Llama-3.2-3B (n_embd=3072) that is
  ~12.3 KB per token per hop, x2 hops per wave.
- The sampled token returns to the client through the whole chain
  (final -> middle -> entry -> client), so every generated token pays
  the full chain's return latency; the fa-link (final -> entry direct)
  proved during Task 19 that direct links between non-adjacent stages
  work fine in this codebase.
- The planner assigns `entry` positionally (first assignment in the
  score-ordered layout, orchestrator.cpp planned_layout_json) with no
  network input -- which is how node-b (Wi-Fi, worst measured link:
  RTT p95 47-107ms from its peers vs 17ms on the wired pair) keeps
  receiving the most latency-critical role. The RTT/jitter/loss matrix
  needed to fix this already exists (`/network/stats`, Task 19 level 2)
  and is currently observability-only.

## Item 1 -- Network-aware role placement (adapt: Petals latency-aware routing)

**Proven where:** Petals builds a full graph of client-server and
server-server latencies plus server speeds and picks the fastest chain
via beam search / D* Lite (arXiv:2209.01188, 2312.08361). GORGO uses
RTT as a scheduling-cost input (arXiv:2602.11688).

**Adaptation here:** the planner already knows per-node scores; add the
measured RTT matrix (pull each node's `/network/stats` at session-create
time) and choose the role ordering that minimizes the chain's total
per-token network cost: sum of hop RTTs over entry->middle->final plus
the entry<->final fa-link RTT (draft delivery is latency-critical --
the whole Task 19 wait-window mechanism exists because of this hop).
Concretely for today's cluster this stops putting `entry` on the Wi-Fi
node whenever a wired node can hold it.

**Effort:** small -- planner-side only, no protocol or worker changes.
**Expected effect:** removes the worst link from the per-token critical
path; based on the measured RTT gap (wired ~6-17ms vs Wi-Fi ~30-107ms
p95), this is plausibly the single largest latency lever available.
**Verify:** A/B the same model+prompts with forced old vs new placement;
compare tok/s and the entry-side arrival_p50/p95 logs.

**Code directions:**
- The split algorithm lives in
  `llama.cpp/tools/distributed/orchestrator/layout_planner/layout_planner.cpp`;
  node ordering is by score (comparator at ~line 185), and role follows
  position in the resulting assignment list (`planned_layout_json`,
  orchestrator.cpp ~1153: index 0 = entry, last = final).
- Add an RTT fetch step to the session-create path in orchestrator.cpp:
  for each candidate node, GET `http://host:port/network/stats` (endpoint
  already exists in node_agent.cpp, struct `peer_rtt_tracker`) and build
  the pairwise RTT matrix from the `peers` map (use `rtt_p95_ms`, fall
  back to `rtt_p50_ms` when sample count is low).
- Scoring: with 3-5 nodes, enumerate all role orderings (n! is tiny) and
  pick the one minimizing `RTT(entry,middle) + RTT(middle,final) +
  RTT(final,entry)` (the last term is the fa-link) subject to the
  existing memory caps. Do not touch layer counts here -- that is Item 4.
- Failure mode to handle: `/network/stats` returns empty peers on a
  freshly restarted node (tracker warms up in ~10s of samples at 1/s).
  Fall back to the current score-order placement when the matrix is
  incomplete, and log which path was taken.

## Item 2 -- Direct final->client token return (adapt: Petals parallel send)

**Proven where:** Petals servers send output activations "to both client
and the subsequent stage" in parallel, since each message is only a few
KB (arXiv:2312.08361 section on inference latency).

**Adaptation here:** the sampled token currently travels
final -> middle -> entry -> client before the client can issue the next
VERIFY. Ship the token from final directly to the client (or to entry's
client loop) in parallel with the normal chain propagation that stages
still need for their KV caches. The fa-link infrastructure from Task 19
is the template: a small direct TCP link carrying token ids, established
at session setup. The chain message doesn't disappear (stages still
consume the accepted tokens), but the client stops waiting on the two
extra hops.

**Effort:** medium -- new link + client-side wait logic; touches
node_agent and split_gen3_c, protocol addition but same wire idioms as
fa-link.
**Expected effect:** cuts up to two hop RTTs off every token's critical
path; on the measured network that is ~10-50ms per token in bad windows.
Interacts positively with speculation: the draft-vs-token race that the
8ms wait window was built around becomes easier for the draft to win.
**Verify:** per-token latency histogram before/after; confirm identical
output streams.

**Code directions:**
- Template is the existing fa-link end to end: orchestrator allocates the
  port (`fa_port = pipe_base + stage_ptrs.size() + 2`, orchestrator.cpp
  ~1792, passed via `cfg["fa_port"]` ~1966/1995), node_agent forwards it
  to the worker args (`start_worker`, node_agent.cpp ~1770-1820, and
  `dist_configure_req.fa_port/fa_host` parsing ~908), split_gen3_c dials
  out. Clone this shape for a `tc_port` (token-to-client) link:
  node_agent (client side, on the entry node) listens, final connects.
- Final side: the token is selected in split_gen3_c's
  `final_process_hidden_item` (both the verify-wave branch and the plain
  branch already know the accepted/bonus token and its position -- the
  same values that go into `st.draft_submit`). Ship `(pos, token_id)`
  on the tc-link right there, BEFORE the chain-return send, mirroring
  how `final_draft_and_ship` uses `split_ab_send_verify_ids`.
- Client side: `run_local_pipeline_generate`'s speculative VERIFY loop
  (node_agent.cpp ~1383) currently blocks in `pipeline_gen3_send_recv`.
  Change to: wait on EITHER the tc-link delivery for the expected pos OR
  the chain response (whichever first); the chain response still carries
  `accepted_ids`/logits bookkeeping, so it must still be consumed -- the
  win is issuing the NEXT verify earlier, not skipping the chain read.
  This makes the loop two-phase; reuse the mailbox/cv idiom from
  split_gen3_c's draft_thread rather than inventing a new one.
- Determinism check: the token stream must be byte-identical with the
  link on and off (this feature moves bytes, it must not change them).

## Item 3 -- Quantized activations on the wire -- **DEMOTED, likely not worth it here**

**Correction (2026-07-22 cleanup):** the original version of this item
adapted Petals' dynamic blockwise activation quantization (arXiv:2312.08361)
on general reasoning. But this project's own Performance Study already
measured and RETIRED this direction: ROADMAP.md lists "Wire format / FP16
hidden / binary protocol / zero-copy / TCP tuning (< 1% each)" under
"Retired directions (do not reopen -- Study evidence)". The physics
agrees: even Qwen2.5-32B's fp32 hidden state is ~20KB/token/hop, which is
~0.16ms at 1Gbps -- noise against a 140ms/token decode and against the
5-100ms RTT variance that actually hurts. Petals' quantization pays off on
~100Mbit internet links, not on this LAN.

**Kept only as a conditional footnote:** revisit ONLY if (a) the cluster
ever runs over a much slower link (true WAN nodes), or (b) prefill of
very long prompts on large models shows transfer time in a perf trace
(n_tokens x 20KB adds up at prefill, unlike decode). Do not build this
on latency grounds; the Study's own measurement stands until a trace
shows otherwise.

## Item 4 -- Measured min-bottleneck layer partitioning (adapt: Petals/SWARM block assignment)

**Proven where:** Petals servers pick their block range to minimize the
swarm's bottleneck (`start = argmin_i sorted([t_i .. t_{i+K-1}])` over
announced per-block throughputs, arXiv:2312.08361); SWARM rebalances
stages by measured queue utilization (arXiv:2301.11913). Hetis/LLM-PQ
validate measured per-device cost models (arXiv:2509.08309, 2403.01136).

**Adaptation here:** replace the score-proportional layer split with an
assignment driven by measured per-node decode throughput (the
registration benchmark already produces decode_tps per node -- the
input data exists). For the single-user latency case, minimize the SUM
of stage times, not the max (per the planner discussion of 2026-07-22:
balancing helps multi-request throughput; a single stream wants total
time minimized -- in practice, push layers toward the fastest node,
node-c, up to its VRAM limit). Keep the current split as fallback.

**Effort:** medium -- planner logic + a calibration pass; no worker
changes (layout already supports arbitrary splits).
**Expected effect:** bounded but real; today's 15/15/34 split for
Qwen-32B already leans toward node-c, but the node-a/node-b 15/15 split
ignores their ~8% score gap and, more importantly, ignores network
placement (couples with Item 1).
**Verify:** tok/s A/B on both a 3B and the 32B model.

**Code directions:**
- The current algorithm is one block:
  `orchestrator/layout_planner/layout_planner.cpp` ~366, comment
  "Score-proportional layer counts, then clamp to per-node memory caps"
  (`exact = n_layer * scores[i] / score_total`, then cap-clamping with
  slack redistribution ~439-458). Replace the proportional formula with
  a measured-time objective; keep the cap-clamping loop as is.
- Input data already flows: nodes report `decode_tps`/`prefill_tps` at
  registration (node_agent.cpp `register_with_orchestrator` ~858; stored
  orchestrator.cpp ~2802-2809 as `node.performance`). First version can
  model per-layer time on node i as `1 / decode_tps_i` normalized --
  i.e., minimize `sum_i(layers_i / decode_tps_i)` for the single-stream
  latency mode instead of equalizing `layers_i / score_i`.
- A later calibration pass can replace registration benchmarks with
  measured per-stage wall times from real sessions: the timing fields
  already returned per generate call (`decode_ms`) plus the entry-side
  perf spans (`ENTRY_COMPUTE_BEGIN/END`) give per-stage numbers without
  new instrumentation.
- Keep the current formula behind the same layout-override mechanism
  that already exists (`layout_override` in `build_and_store_install_plan`,
  orchestrator.cpp ~684) so A/B is a config choice, not a rebuild.

## Item 5 -- Relayout hysteresis threshold (adapt: Petals >=20% rebalancing rule)

**Proven where:** Petals nodes only trigger rebalancing when the
predicted total-throughput gain is at least p=20%, explicitly to
balance efficiency against the cost of cache invalidation
(arXiv:2312.08361).

**Adaptation here:** whenever Items 1/4 produce a planner that can
propose a better layout for an EXISTING session or model install, gate
the actual migration on a predicted-gain threshold (start at 20%,
borrowed directly) so a borderline-better layout never thrashes weights
across nodes. This is the same hysteresis discipline already used in
the THROTTLED wait mechanism, applied at the planner level. For now
this only gates re-layout on session creation (live migration is Task
19 level 4, explicitly out of scope).

**Effort:** small, but only meaningful once Item 1 or 4 exists.

## Explicitly NOT adopted (and why)

- **Tensor parallelism** (all datacenter frameworks): 2 all-reduce per
  layer needs NVLink-class interconnect; strictly worse on this network
  (survey part4 section 2). Same conclusion Petals reached.
- **RadixAttention-style prefix caching** (SGLang): optimizes repeated
  shared prefixes across many concurrent users; single-user homelab
  sees little of that workload. Revisit if the dashboard grows a
  multi-user story.
- **Disaggregated prefill/decode pools** (DistServe/Splitwise): needs
  ~90Gbps to hide KV transfer (survey part4 section 6); our KV must
  stay put.
- **DHT-based discovery** (Petals): right-sized for an open swarm of
  hundreds of volunteer peers; a 3-5 node orchestrated cluster gets
  nothing from it over the existing registration protocol.

## Suggested order

1. Item 1 (network-aware placement) -- smallest effort, likely largest
   single win, uses infrastructure that already exists.
2. Item 2 (direct token return) -- biggest protocol-level latency cut,
   template already proven in-repo by the fa-link.
3. Item 4 + Item 5 (measured partitioning + hysteresis) -- planner
   depth, builds on Item 1's plumbing.
4. Item 3 -- demoted per the Study's retired-directions evidence (see
   the item); only revisit on WAN links or a prefill trace showing
   transfer cost.

Re-run the docs/bench/2026-07-21_wait_policy methodology (same prompts,
sequential sessions, /network/stats recorded per run) after each item
lands, against the recorded baseline, so each adaptation gets its own
honest before/after number instead of a bundled "it got faster".
