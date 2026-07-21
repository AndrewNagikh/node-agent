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

## Item 3 -- Quantized activations on the wire (adapt: Petals dynamic blockwise quantization)

**Proven where:** Petals compresses inter-stage activations with dynamic
blockwise quantization, roughly halving bandwidth needs "without
noticeable effect on generation quality" (arXiv:2312.08361); weight-side
8-bit/NF4 quantization ships in the same system.

**Adaptation here:** quantize the hidden-state payload in
split_tcp_send_hidden to int8 with per-block scales (block size ~64-128
floats), dequantize on receive. ~4x smaller payloads (12.3KB -> ~3.2KB
per token for 3B; proportionally more for larger models -- Qwen2.5-32B
n_embd=5120 is ~20KB/token today).

**Effort:** medium -- symmetric encode/decode in the transport layer,
behind a protocol version/flag so mixed-version clusters fail cleanly.
**Trade-off to state honestly:** this changes computed values slightly,
so the bit-identical-to-baseline determinism property validated in Task
19 no longer holds in quantized mode. Petals' published result says
quality impact is negligible, but ours must be re-verified (same-prompt
output comparison + a small quality spot-check) before making it the
default. Keep a switch.
**Expected effect:** on a ~5-9ms-RTT link the serialization/transfer
share per hop shrinks; matters most for prefill (n_tokens large) and for
larger models; also reduces jitter sensitivity (fewer packets per wave).
**Verify:** tok/s A/B plus output-quality comparison, per the trade-off
above.

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
4. Item 3 (wire quantization) -- solid win but carries the determinism
   trade-off, so it goes last and stays switchable.

Re-run the docs/bench/2026-07-21_wait_policy methodology (same prompts,
sequential sessions, /network/stats recorded per run) after each item
lands, against the recorded baseline, so each adaptation gets its own
honest before/after number instead of a bundled "it got faster".
