# Research 17 — Distributed Inference Performance Ceiling Study

**Type:** Research / Architecture Investigation / Performance Modeling
**Implementation:** ❌ None
**Code changes:** ❌ Forbidden — this document contains no runtime, protocol, GGML, or scheduler changes
**Data basis:** Task 12–16 traces and reports (see §16); homelab `trace-000010`, `trace-000024`, Docker `trace-000002`; LAN benchmark `20260706_200851`
**Primary cluster:** entry M3 Pro → middle M1 Pro → final RTX 4070 Ti, 1 GbE LAN, TinyLlama 1.1B Q4

---

## 1. Executive Summary

**Primary research question:** what fundamentally prevents this distributed runtime from approaching local inference throughput?

**Answer in one paragraph:** Nothing about the network, the wire format, or the transport prevents it — those are measured at < 1% of token time. The gap between local **~125 tok/s** and distributed **25.8 tok/s** decomposes into three classes: **(a) architectural scheduling costs** (orchestrator serial dispatch bubble, 11.7 ms = 30% of period), **(b) implementation costs that look like physics but are not** (GPU sync placed on the critical path 4.7 ms, sampling path 5.2 ms, per-stage graph submit overhead ~9 ms of compute inflation), and **(c) one genuinely fundamental law**: single-stream autoregressive decode over a pipeline gains *nothing* from parallelism — every token must traverse all stages serially, so pipeline distribution can only *add* latency relative to the fastest single node that fits the model. The system is not close to any fundamental limit today: it runs at **~15% of its own physics ceiling**, while local llama.cpp runs at **~90%** of its ceiling.

### Headline findings

| # | Finding | Evidence |
|---|---------|----------|
| F1 | **Distributed pipeline can never beat a single node that fits the model.** For single-stream decode, per-token latency is the *sum* of stage times; splitting a model that fits on the RTX 4070 Ti across 3 nodes raises the compute floor from 2.2 ms to 3.9 ms and adds hops. Distribution is a **capacity** technology, not a **speed** technology. | §10 theorem, §9 tables |
| F2 | **The system runs at ~15% of its theoretical ceiling** (25.8 tok/s vs ~162–199 tok/s physics ceiling for TinyLlama on this cluster). The 85% loss is scheduling + API-contract + implementation, not network. | §9.4 |
| F3 | **Network is already solved.** B→C hop 0.04 ms, TCP send ~0.17 ms, memcpy 0.003 ms, wire 8 KB/token. FP16 wire, binary protocol, zero-copy are all < 1% levers. | Task 15.1, Task 16 §4.3 |
| F4 | **The middle stage proves the runtime can be efficient.** Middle computes its layer share in 2.21 ms vs a 1.86 ms bandwidth-model floor (**84% efficiency**). Entry (8.79 ms vs ~2.5 ms floor) and final (5.68 ms vs ~0.7 ms floor) carry the endpoint overhead: embedding path, D2H, logits, graph submit, sampler. | §6.3 |
| F5 | **Small models cannot be served competitively by this (or any) LAN pipeline; large models can.** Fixed per-token overhead (~22 ms today, ~1.5 ms theoretical floor) dominates small models and amortizes for large ones. The crossover where distribution *wins outright* is memory capacity: models > ~12 GB (fastest node's VRAM). | §10 |
| F6 | **Startup is ~10–60 s warm vs ~1 s local, and nothing in it is fundamental except first-time weight distribution.** Session create (8.8–61.5 s) is worker spawn + per-node model load + READY polling — all removable with persistent workers / TensorProvider / event-driven readiness. | §7, §8 |
| F7 | **The current architecture can realistically reach ~50–60% of local single-stream throughput for TinyLlama-class models; 80–95% requires speculative decoding (pipeline-friendly) or a different success metric.** For ≥14B models the same architecture can reach 80–90%+ of its *own* ceiling, and for >12 GB models it is the only option at all. | §13 |

### The three levers that matter (everything else is noise)

1. **Kill the dispatch bubble** (async orchestrator, RFC-0013 Phases 4–5 already built): +43% → 36.9 tok/s.
2. **Get the GPU sync and sampling off the serial path** (overlap sync; sample on final GPU, return 4-byte token): +30% cumulative → ~56 tok/s.
3. **Reduce endpoint compute inflation** (graph submit reuse / Metal-CUDA graph capture on entry & final): → ~70–75 tok/s (~55–60% of local).

Beyond that, single-stream pipeline physics is exhausted; the only known multiplier is **speculative decoding** (§12, §13).

---

## 2. Method and Data Discipline

All measured numbers in this study come from existing artifacts; no new runs were made. Following `PERFORMANCE_METRICS_SPEC.md`:

- **Client period** (`decode_ms / tokens`) is throughput ground truth — homelab **38.81 ms/token = 25.77 tok/s** (`trace-000010`).
- **Serial critical path** = `entry_compute + transfer_ab + middle_compute + transfer_bc + final_compute + sampling` — homelab **27.08 ms**.
- **Bubble** = period − critical path — homelab **11.73 ms (30%)**; Docker CPU **40.8 ms (75%)**.
- Cross-node wall timestamps are clock-skewed; span durations are authoritative (Task 13.1/14 rules).

Calculated (not measured) numbers are labeled **[model]** and use the calibrated constants of §9.1. Where a number is an estimate from vendor specs or literature rather than a trace, it is marked **[est]**.

---

## 3. Current Runtime Performance Model

### 3.1 The decode period equation

Steady-state single-stream decode on the current stack:

```
T_token = B + E + G + M + H_bc + F + S

B     orchestrator dispatch bubble          11.73 ms   (architectural)
E     entry compute (embed + layers 0..k)    8.79 ms   (mandatory + inflation)
G     gather = GPU sync + D2H + pack + send  5.18 ms   (4.72 ms = sync; API contract)
M     middle compute (layers k..m)           2.21 ms   (mandatory, near-floor)
H_bc  middle→final transport                 0.04 ms   (mandatory, negligible)
F     final compute (layers m..n + head)     5.68 ms   (mandatory + inflation)
S     sampling + return path                 5.18 ms   (mostly implementation)
────────────────────────────────────────────────────
      38.81 ms  →  25.77 tok/s
```

### 3.2 Dependency structure (why this is a sum, not a max)

Autoregressive decode: token N+1's input **is** token N's sampled output (RFC-0013 §12). For a single sequence there is never a second wave whose compute could fill the pipeline — stages idle whenever their slice of the current token is done. Therefore:

- **Within one token:** stages are serial by data dependency (E → G → M → H → F → S).
- **Across tokens:** strictly serial by the autoregressive law.
- **What overlap can hide:** only costs *not* on the token's data path — the orchestrator bubble, the entry GPU sync (if the next dispatch doesn't need the synced buffer), bookkeeping.

Consequence: **pipeline utilization for one stream is fundamentally ≤ (1/N stages)** per stage; measured `utilization.json` shows the middle stage saturated only because it is measured against its own busy window, while entry/final idle between tokens.

### 3.3 Cross-environment check

| Metric | Homelab GPU | Docker CPU |
|--------|------------:|-----------:|
| Client TPS | 25.8 | 15.4 |
| Period | 38.8 ms | 54.4 ms |
| Serial critical path | 27.1 ms | 13.6 ms |
| Bubble | 11.7 ms (30%) | 40.8 ms (75%) |

Same architecture, opposite dominant limiter: Docker CPU is bubble-bound; homelab is (bubble + work)-bound. Any model of "what limits TPS" must explain both — the equation of §3.1 does.

---

## 4. Local vs Distributed Comparison

| Aspect | Local llama.cpp (M3 Pro) | Distributed (3-node) |
|--------|--------------------------|----------------------|
| TinyLlama decode | 120–130 tok/s (~8.0 ms/token) | 25.8 tok/s (38.8 ms/token) |
| Fraction of own physics ceiling | ~90% (§9.1) | ~15% (§9.4) |
| Weights traversal per token | 1× full model, one address space | 1× full model, 3 address spaces |
| Hidden state ownership | never leaves GPU until logits | GPU→host→TCP→host→GPU **twice** |
| Sync points per token | 1 (implicit at logits read) | 3+ (per-stage result APIs) |
| Scheduler | in-process loop, ~0 cost | HTTP orchestrator + blocking RPC round-trip |
| Sampling | in-process, on logits buffer | on final worker + return path through pipeline |
| Startup | mmap GGUF (~1 s) → generate | registry → sync → materialize → configure → graph reserve → READY → session (§7) |

The structural difference is not "network" — it is that local inference has **one owner** (one process, one device timeline, one scheduler tick per token) while the distributed runtime has **five ownership transfers and two schedulers** (orchestrator + per-worker loops) per token.

---

## 5. Runtime Cost Taxonomy (Objective B)

Every millisecond of the 38.81 ms token, classified. "Mandatory" = required by physics or the autoregressive law in *any* architecture on this hardware. "Architectural" = consequence of this design's structure (removable by redesign). "Implementation" = artifact of current code/API usage (removable without redesign).

| Cost | ms | Class | Justification |
|------|---:|-------|---------------|
| GPU compute, entry layers (bandwidth floor) | ~2.5 **[model]** | **Mandatory** | Weights must be read once per token: 0.22 GB / 90 GB/s eff |
| Entry compute inflation (submit, batch prep, embed path, D2H queue) | ~6.3 | **Implementation** | Middle proves ~0.32 ms/layer is achievable; entry runs ~1.1 ms/layer |
| GPU sync wait (`LLAMA_BACKEND_SYNCHRONIZE`) | 4.72 | **Architectural/API** | The *wait* is mandatory before host read (Task 15.2), but host read itself and its placement on the serial path are design choices |
| Gather residual (access, unattributed, alloc) | 0.42 | Implementation | Task 15.1b |
| A→B TCP send | ~0.17 | **Mandatory** | Cross-machine serialization is fundamental (Task 15.3 §5) |
| Middle compute | 2.21 | **Mandatory (84%)** | 1.86 ms bandwidth floor; near-optimal |
| B→C transport | 0.04 | **Mandatory** | Negligible |
| Final compute (bandwidth floor incl. head) | ~1.0 **[model]** | **Mandatory** | 0.22 GB + head on 302 GB/s eff |
| Final compute inflation | ~4.7 | **Implementation** | Same class as entry: submit + logits handling on CUDA path |
| Sampling + token return | 5.18 | **Implementation (mostly)** | Sampling itself is sub-ms locally; 5 ms = logits access + sampler chain + return framing. KV update is inside compute spans |
| Orchestrator dispatch bubble | 11.73 | **Architectural** | Serial blocking RPC per token (Task 12); autoregressive law does **not** require it (RFC-0013 §12) |
| **Total** | **38.81** | | |

**Class totals:**

| Class | ms | % of period |
|-------|---:|------------:|
| Mandatory (this cluster, this split) | ~6.9 | 18% |
| Architectural (bubble + sync placement) | ~16.5 | 42% |
| Implementation (inflation + sampling + residuals) | ~15.4 | 40% |

> **Reading:** 82% of every token is removable in principle. The mandatory floor on the *current equal-layer split* is ~6.9 ms → ~145 tok/s; on a bandwidth-proportional split ~5.0 ms → ~199 tok/s (§9).

Session-scope costs (not per-token) are classified in §7.

---

## 6. Decode Cost Model — deep dives

### 6.1 The bubble (11.7 ms homelab / 40.8 ms Docker)

Task 12 proved the orchestrator dispatches tokens strictly serially: send DECODE → block until the full E→M→F→sample→return unwind completes → send next. RFC-0013 Phases 3–5 (entry queue, stage queues, client pipelining) were built and are default-on since Task 13.6, yet the Task 14/16 homelab trace still shows an 11.7 ms inter-token gap. **Open question flagged for the roadmap:** verify which v2 flags were actually active in `trace-000010`; either the client pipeline was off, or the pipelined path still serializes ~12 ms of orchestrator work (HTTP handling, token commit, response bookkeeping) between waves. Docker Task 13.5 measurement (bubble still 72% with pipelining on, "embedding-bound") suggests the second: pipelining moved the block, not removed it.

### 6.2 The sync (4.72 ms)

Task 15.2 established: `llama_decode()` intentionally returns after *submitting* GPU work; `llama_get_embeddings()` then performs the mandatory wait. The wait is physics (GPU must finish before host reads); the **serialization of that wait into the gather/transport step** is an API-placement artifact. In a local run the same wait exists but is the *only* sync per token and overlaps nothing; here it sits between entry compute and the A→B send, extending the serial path. The theoretical fix classes (no implementation): relocate (`llama_synchronize` after decode), overlap with pipeline (15.2 Option C), or eliminate host read entirely (GPU-resident handoff — blocked by process isolation, Task 15.3 §7).

### 6.3 Compute inflation — the quiet 9 ms

Comparing measured stage compute against the bandwidth model (§9.1):

| Stage | Node | Measured | Floor [model] | Ratio | Per-layer |
|-------|------|---------:|--------------:|------:|----------:|
| Entry (embed + ~8 layers) | M3 Pro | 8.79 ms | ~2.5 ms | **3.5×** | ~1.10 ms |
| Middle (~7 layers) | M1 Pro | 2.21 ms | ~1.86 ms | **1.2×** | ~0.32 ms |
| Final (~7 layers + norm + head) | 4070 Ti | 5.68 ms | ~1.0 ms | **5.7×** | ~0.81 ms |

The middle stage — the only stage that does *nothing but layers* — is within 20% of physics. Entry and final, which carry the embedding path, D2H staging, logits, graph submit, and result APIs, are 3.5–5.7× over. This localizes the inflation to **endpoint responsibilities**, not to GGML layer kernels, and is consistent across backends (Metal entry, CUDA final). Likely composition **[est]**: per-token graph submit/command-buffer overhead (`GGML_GRAPH_EXECUTE` 0.37 ms is CPU submit only), `n_ubatch=1` graph shape, output-buffer staging, small-kernel launch overhead on partial graphs.

### 6.4 Sampling (5.18 ms)

Sampling already runs on the final worker (correct placement). The cost is the *path*: logits materialization/access + sampler chain + response framing back through the pipeline. Local llama.cpp spends well under 1 ms here. Classification: ≥ 4 ms implementation.

### 6.5 Transport (0.04–0.2 ms) — closed

Task 15.1: gather 5.71 ms of the 5.86 ms "pack" total is `llama_get_embeddings` (= sync); memcpy 0.003 ms; frame build 0.119 ms; socket send 0.056 ms. Payload 8 KB/token (n_embd 2048 × fp32). **Any wire-format work (fp16, binary framing, zero-copy) is bounded by ~0.2 ms = < 1% of period.** This study formally retires transport as an optimization target for decode.

---

## 7. Startup Cost Model

### 7.1 Measured lifecycle (homelab `homelab_full_20260706`, Docker `task11_docker`)

```
Install/Sync ──► Coverage ──► Session create ──────────────────────────► TTFT
 (cold only)      READY        (prepare → configure → load → reserve →
                                listener → READY poll → session)
```

| Stage | Homelab (TinyLlama) | Homelab (qwen8b) | Docker (TinyLlama) | Local llama.cpp |
|-------|--------------------:|-----------------:|-------------------:|----------------:|
| Sync (cold) | 286 s | 465 s | 60.6 s | — (file already local) |
| Coverage → READY | included | included | 0.36 s | — |
| Session create | 10.3 s | 61.5 s | 1.81 s | — |
| Model load | (inside session) | (inside session) | (inside session) | ~0.5–2 s mmap **[est]** |
| TTFT (warm, 16-tok prompt) | 111 ms (trace) – 1.46 s (E2E) | 1.05 s | 0.62 s | ~0.1 s **[est]** |

### 7.2 Per-stage verdicts (the four required questions)

| Stage | Why it exists | Fundamental? | Implementation-specific? | Can it disappear? |
|-------|---------------|--------------|--------------------------|-------------------|
| **Registry / manifest** | Orchestrator must know tensor layout without loading GGUF (Task 10 OOM fix) | Metadata resolution is fundamental to *distribution* | HTTP-range header reads — cheap | Cacheable → amortizes to ~0 |
| **Sync (layer install)** | Weights must physically reside on the executing node | **Yes — first time.** Information must move once | Throughput 1.3–14 MiB/s is implementation (single-stream HTTP, blob granularity) | Warm cache → 0. Cold: floor = model_bytes / LAN_bandwidth (0.67 GB / 1 GbE ≈ 6 s, vs 286 s measured → **~50× implementation overhead** in the sync path) |
| **Materialization (worker GGUF)** | Workers load via `llama_model_load` which wants a GGUF | No | **Yes** — Task 11 interim; `worker_gguf_bytes=0` already, latency 6–16 ms | Yes (Task 11.7 TensorProvider) |
| **Configure / worker spawn** | Fresh worker processes per session | No — process-per-session is a lifecycle choice | Yes | Yes → **persistent workers** keep model + context resident |
| **Context creation + KV alloc** | Per-session KV cache must exist | KV memory itself: yes | Allocation per session: partially | Pool/reuse across sessions |
| **Graph reserve** | GGML pre-plans worst-case graph | One-time per (model, ctx-shape): semi-fundamental | Re-done per session: implementation | Cache per (model, n_ctx) → once per process lifetime |
| **READY polling** | Orchestrator waits for worker state machine | Readiness itself: yes | **Polling** (300 s limits, coarse intervals): yes | Event-driven callbacks → sub-second |
| **Session create RPC chain** | Control-plane bookkeeping | Registration: trivial ms | Serialized per-node HTTP round trips | Parallelize + persist → sub-second |

**Startup verdict:** with warm blob cache, persistent workers, TensorProvider, and event-driven readiness, the *architecture* supports session create ≈ **sub-second** (control-plane only) — a ~10–60× reduction — without touching the decode path. The only irreducible cold cost is one model transfer over the LAN, whose current implementation runs ~50× slower than the wire allows.

### 7.3 Startup robustness ceiling

qwen14b fails `session_create` (HTTP 500 at 93.6 s, orchestrator error not persisted). Not a performance limit, but it currently caps validated scale at 8B on homelab and blocks the region (≥ 14B) where distribution actually wins (§10). Startup work is therefore *prerequisite* to demonstrating the architecture's real value proposition.

---

## 8. Model Loading Investigation — why local starts faster

```
Local:        open GGUF ─ mmap ─ (lazy page-in) ─ graph reserve ─ generate
Distributed:  registry ─ manifest ─ layout ─ install plan ─ N× blob download
              ─ coverage reconcile ─ N× runtime/prepare ─ N× configure/spawn
              ─ N× sparse tensor load ─ N× graph reserve ─ N× listener
              ─ READY poll loop ─ session registry ─ generate
```

Every additional distributed stage exists for exactly one of three reasons:

1. **Weights are remote** (sync, coverage) — fundamental once, cacheable after.
2. **Execution is multi-process/multi-host** (prepare, configure, spawn, listener, READY) — consequence of the process-per-stage architecture; a persistent-worker design pays it once per *deployment*, not per session.
3. **Control/data-plane separation** (registry, manifest, layout, install plan) — the price of the orchestrator not holding models (Task 10); milliseconds when cached, and worth keeping.

Local llama.cpp has none of these because it has one process, one file, one lifetime. The distributed stack cannot delete category 2 stages, but it can change their **frequency** from per-session to per-deployment — which is what "fast startup" means here.

---

## 9. Theoretical Ceiling (Objective A) — calculated, not measured

### 9.1 Model and calibration

Single-stream decode is memory-bandwidth-bound: each token must read all (active) weight bytes. Per-token time for an N-stage pipeline:

```
T_token(N) = Σ_i  W_i / (η · BW_i)   +   (N−1) · t_hop   +   t_sample
```

- `W_i` — weight bytes on node i; `BW_i` — peak memory bandwidth
- `η` — effective bandwidth efficiency. **Calibrated from the local baseline:** M3 Pro, TinyLlama 0.67 GB, 120–130 tok/s → ~87 GB/s effective / 150 peak → **η ≈ 0.6**
- `t_hop` — per-hop LAN cost: 8–20 KB payload on 1 GbE + latency ≈ **0.3 ms**
- `t_sample` — logits handoff + sampler floor ≈ **0.5 ms**

Effective bandwidths: M3 Pro **90**, M1 Pro **120**, RTX 4070 Ti **302 GB/s**; aggregate 3-node **512 GB/s**.

Model weight sizes (Q4, from benchmark artifacts where available): TinyLlama **0.67 GB**, 1B **0.75**, 3B **1.9 [est]**, 8B **4.7**, 14B **7.9**, 30B dense-equivalent **17.5 [est]** (qwen3-30b is MoE-A3B: ~17.5 GB resident but only ~2.2 GB active bytes/token **[est]** — noted separately).

Two split policies:

- **Equal layers** (current planner): `T_compute = W/3 · (1/90 + 1/120 + 1/302)` = **7.58 ms/GB**
- **Bandwidth-proportional**: `T_compute = N · W / ΣBW` = **5.86 ms/GB** (3-node)

### 9.2 Ceiling tables — tok/s **[model]**

**By cluster size (bandwidth-proportional split; hops = (N−1)·0.3 ms; +0.5 ms sample):**

| Model | 1 node (4070 Ti, 302) | 2 nodes (+M1 Pro, 422) | 3 nodes (+M3 Pro, 512) | 4 nodes (+2nd M1 Pro, 632) |
|-------|----------------------:|-----------------------:|-----------------------:|---------------------------:|
| TinyLlama 0.67 GB | **368** | 251 | 199 | 177 |
| 1B 0.75 GB | 335 | 236 | 182 | 160 |
| 3B 1.9 GB | 147 | 102 | 82 | 74 |
| 8B 4.7 GB | 62 | 43 | 35 | 32 |
| 14B 7.9 GB | (37)* | 26 | 21 | 19 |
| 30B dense 17.5 GB | ✗ no fit | 12* | 9.6 | 8.9 |
| 30B MoE (~2.2 GB active) | ✗ no fit | 44* | 61** | 55 |

\* borderline memory fit (12 GB VRAM / ~23 GB 2-node). \*\* MoE active-bytes model; routing overhead not included.

**Current planner (equal layers, 3 nodes) vs bandwidth-proportional:**

| Model | Equal-layer ceiling | BW-proportional ceiling | Planner leaves on the table |
|-------|--------------------:|------------------------:|----------------------------:|
| TinyLlama | 162 | 199 | +23% |
| 8B | 27 | 35 | +30% |
| 14B | 16.4 | 21 | +28% |
| 30B dense | 7.5 | 9.6 | +28% |

### 9.3 Why scaling behaves differently as model size grows

`T_token = C·W + F` where `C` is the aggregate-bandwidth compute slope and `F` the fixed per-token tax (hops + sampling + dispatch + sync). Distributed efficiency = `C·W / (C·W + F)`:

- **TinyLlama:** C·W ≈ 3.9 ms vs today's F ≈ 35 ms (incl. inflation) → efficiency 10–15%. Even at the theoretical F = 1.1 ms → 78%.
- **8B:** C·W ≈ 27.5 ms → today's F would still cost half; theoretical F → 96%.
- **30B dense:** C·W ≈ 102 ms → even the *current, unoptimized* F ≈ 22 ms yields 82% of ceiling.

**Small models are inefficient because the token is too cheap to hide the fixed tax. Large models amortize it.** This is the single sentence that explains every scaling observation in the benchmark reports.

### 9.4 Reality check against traces

| Quantity | Measured | Ceiling [model] | % of ceiling |
|----------|---------:|----------------:|-------------:|
| Local M3 Pro TinyLlama | 120–130 tok/s | ~139 | **~90%** |
| Distributed 3-node TinyLlama | 25.8 tok/s | 162 (equal-layer) | **~16%** |
| Distributed stage compute sum | 16.68 ms | 5.08 ms | 3.3× over floor |

The 6× local-vs-distributed gap is **not** in the ceilings (139 vs 162 — the cluster is theoretically *faster* than the M3 Pro for TinyLlama); it is entirely in the 85% gap between the distributed runtime and its own ceiling.

---

## 10. Scaling Study — answers

**Q: Why are small models inefficient?** Fixed per-token tax F dominates the tiny compute term (§9.3).

**Q: At what model size does distributed become beneficial?** Two thresholds:

1. **Beneficial vs. *nothing*** (model doesn't fit the best node): W_q4 + KV + overhead > ~12 GB → **≥ 14B dense** on this cluster. Here distributed throughput is infinitely better than the alternative.
2. **Beneficial vs. local on the best node** (model fits): **never** — see the theorem below.

**Theorem (single-stream pipeline).** For one autoregressive stream, `T_dist = Σ W_i/(η·BW_i) + hops + F ≥ W/(η·BW_max) = T_best_node` whenever the model fits on the fastest node, because moving any byte share from the fastest node to a slower node increases the sum, and hops/F are non-negative. Distribution helps **capacity** (and multi-client throughput via batching), not single-stream latency. Corollary: benchmarking TinyLlama distributed against TinyLlama local measures overhead, not value; the honest KPI is **% of the cluster's own ceiling** and **largest servable model**.

**Q: When does network stop dominating?** It never started. Decode network cost is 0.04–0.2 ms (<1%) at every model size — hidden grows only linearly with n_embd (8 KB → ~20 KB fp32 at 14B). The only network-sensitive phase is **prefill** (prompt_len × hidden per hop: 2 k tokens × 20 KB ≈ 40 MB ≈ 0.35 s on 1 GbE **[est]**) and **cold sync**. "Network dominates" was an artifact of mislabeled GPU sync (Task 15.1b).

**Q: When does compute dominate?** Already at 8B (C·W ≈ 27 ms > F ≈ 22 ms current), and overwhelmingly at 14B+.

**Q: What cluster sizes make sense?** Rule derived from the ceiling model: **adding a node reduces per-token latency only if its effective bandwidth exceeds the current per-node average** (`BW_new > ΣBW/k`), because `T = N·W/ΣBW`. On this cluster: adding the M3 Pro to {4070 Ti, M1 Pro} *raises* the TinyLlama ceiling time (2-node 251 tok/s → 3-node 199); adding a second 4070 Ti would lower it. Add slow nodes only for **memory capacity**, and give them the smallest byte share the memory constraint allows.

**Scaling curves (3-node, tok/s [model]):**

```
              ceiling(BW-prop)   ceiling(equal)   current-F (22ms tax)   today
TinyLlama          199                162                  26              25.8
1B                 182                147                  25
3B                  82                 64                  19
8B                  35                 27                  12
14B                 21                 16                   9
30B dense           9.6                7.5                  6
```

(The "current-F" column freezes today's fixed tax and shows measured 25.8 aligns with the model — validating the equation.)

---

## 11. Comparison With Existing Systems (Objective C)

Knowledge-based survey (cutoff Jan 2026); purpose is tradeoff extraction, not implementation copying.

| System | Execution model | Parallelism | Sync model | Scheduler | KV handling | Sampling | Transport | Session/startup |
|--------|-----------------|-------------|------------|-----------|-------------|----------|-----------|-----------------|
| **TensorRT-LLM** | Ahead-of-time compiled engine, in-flight (continuous) batching | TP + PP, NCCL | CUDA graphs; stream events, no host round-trips in decode loop | C++ executor, async request queue | Paged KV, GPU-resident | On-GPU (logits never leave device) | NCCL / MPI | Engine build offline (minutes); load fast; persistent server |
| **vLLM** | Persistent async engine loop, continuous batching | TP (NCCL) + PP + optional multi-node (Ray) | Per-step GPU sync hidden by batch scale | Central scheduler ticks per iteration; CPU overlap via async output processing | **PagedAttention** (block tables), GPU | On-GPU sampler | NCCL; ZMQ/Ray control | Load once, serve forever |
| **SGLang** | Persistent engine; **zero-overhead scheduler** explicitly overlaps CPU scheduling with GPU compute | TP; PP; data parallel | Same-device events | Overlap scheduler — the direct solution to *our* bubble | RadixAttention prefix cache | On-GPU | NCCL | Persistent |
| **DeepSpeed-Inference/MII** | Kernel injection into HF models; dynamic SplitFuse batching | TP; ZeRO-offload for capacity | Stream-based | Async batching server | GPU (+ CPU/NVMe offload for capacity) | On-GPU | NCCL | Persistent |
| **Megatron-LM** | Training-first; inference via 1F1B pipeline with micro-batches | TP (NVLink-class only) + PP | Collective-based | Static schedule | — | — | NCCL/NVLink | Long-lived jobs |
| **Exo** | Consumer-device LAN pipeline (closest cousin) | PP over ring, memory-weighted partitioning | Per-hop host serialization (same problem class as ours) | Peer-to-peer, no central orchestrator | Per-node | On final node | gRPC over LAN | Persistent daemons, discovery |
| **Petals** | Internet-scale layer servers | PP with routing/failover | High-latency tolerant | Client-driven routing | Server-held attention caches per session | Client-side | HTTP/libp2p, WAN | Servers persistent; throughput via batching, single-stream slow by design |
| **llama.cpp RPC backend** | ggml ops proxied to remote backend | Op-level offload | Synchronous per-op RPC | Single host scheduler | Host-controlled | Host | TCP | Simple; latency-bound per op |

### Tradeoff extraction — how the fast systems avoid each of our cost classes

| Our cost | Their solution | Applicability here |
|----------|----------------|--------------------|
| Dispatch bubble (11.7 ms) | Persistent async engine; scheduler tick overlapped with GPU compute (SGLang's "zero-overhead" scheduler is literally this fix) | Direct — RFC-0013 Phase 5 is the same idea; needs to actually engage on homelab |
| GPU sync + D2H per stage (4.7 ms) | Hidden state **never touches host**: TP allreduce or PP send is device-to-device (NCCL/NVLink/RDMA) | Partially — cross-vendor (Metal↔CUDA) + 1 GbE means host staging stays; the *placement/overlap* of sync is still ours to fix |
| Sampling path (5.2 ms) | Sample on the GPU that computed logits; only a 4-byte token id crosses any boundary | Direct |
| Compute inflation (endpoint 3.5–5.7×) | CUDA graph capture / compiled engines amortize per-token submit to ~0 | Direct concept (Metal command-buffer reuse, CUDA Graphs) |
| Session startup | Load once, persistent server; sessions are rows in a table, not process trees | Direct |
| Single-stream latency wall | (a) TP on NVLink-class interconnects — **not viable on 1 GbE** (2 allreduces × n_layers × ~0.3 ms ≈ 13+ ms/token latency floor); (b) **speculative decoding** — draft model proposes k tokens, pipeline verifies them in one pass; turns the pipeline's weakness (per-pass latency) into amortization | (b) is the only known multiplier for this topology |
| Small-model inefficiency | Nobody solves it; production systems batch many streams so per-stream overhead is amortized (continuous batching) | Reframes the goal: single-stream small-model parity is not a target any system achieves over LAN |

**Key architectural contrast:** every fast system is **engine-centric** (one long-lived process per GPU owning weights, KV, scheduler state, with devices talking directly) — while this project is currently **transaction-centric** (per-session process trees, host-mediated handoffs, orchestrator round-trips). The protocol-v2 work is precisely the migration from the second model toward the first; the comparison confirms the direction and says none of the remaining gap requires exotic hardware to shrink to the ~50–60% band (§13).

---

## 12. Performance Gap Attribution (Objective D)

Local M3 Pro ≈ 125 tok/s (8.0 ms). Distributed = 25.8 tok/s (38.81 ms). **Gap = 30.8 ms/token = 99 tok/s.**

Attribution is done in **milliseconds** (additive, trace-backed); tok/s equivalents are shown as the cumulative recovery sequence, because tok/s do not decompose additively (1/x).

### 12.1 Attribution tree (ms space)

```
38.81 ms distributed token  −  8.0 ms local token  =  30.8 ms gap
│
├── 11.73 ms  Orchestrator / scheduling bubble            [architectural]
│    └── serial dispatch RPC; autoregressive law does not require it
├──  4.72 ms  Entry GPU sync serialized into gather       [architectural/API]
│    └── wait is physics; its position on the serial path is not
├──  4.7  ms  Sampling-path excess (5.18 − ~0.5 local)    [implementation]
│    └── logits access + sampler chain + return framing on final
├──  6.3  ms  Entry compute inflation (8.79 − ~2.5 floor) [implementation]
│    └── graph submit, batch prep, embed path, D2H queue; M3 Pro slower node
├──  4.7  ms  Final compute inflation (5.68 − ~1.0 floor) [implementation]
│    └── same class on CUDA; incl. logits materialization
├── −1.9  ms  Middle stage credit                          [hardware]
│    └── middle layers run on M1 Pro/4070 Ti faster than they would on M3 Pro;
│        the cluster's aggregate hardware is *better* than the local baseline
├──  0.46 ms  Gather residual + A→B framing/send          [implementation]
└──  0.04 ms  B→C network                                  [mandatory]
─────────────
   ≈ 30.8 ms  ✔ fully attributed (±0.1 ms rounding)
```

### 12.2 Cumulative recovery sequence (what-if, ordered by roadmap)

| Step | Remove | Period | tok/s | % of local 125 |
|------|--------|-------:|------:|---------------:|
| today | — | 38.81 | 25.8 | 21% |
| 1 | bubble (11.73) | 27.08 | 36.9 | 30% |
| 2 | + sync off path (4.72) | 22.36 | 44.7 | 36% |
| 3 | + sampling excess (4.7) | 17.66 | 56.6 | 45% |
| 4 | + endpoint inflation (9 of 11) | ~8.7–13.7 | ~73–115 | 58–92%\* |
| 5 | + partition by bandwidth | §9.2 ceiling 199 | — | — |

\* Step 4's upper range assumes near-total elimination of submit overhead — optimistic; **~70–75 tok/s (55–60%) is the defensible engineering estimate** for the current architecture (§13).

**Where did the 94–99 tok/s go?** In order of ms: scheduling bubble (38%), endpoint compute inflation (36%), sampling path (15%), GPU sync placement (15%), minus a 6% hardware credit; network ≈ 0.

---

## 13. Long-Term Architecture — can this design reach 80/90/95% of local?

**For TinyLlama-class (fits everywhere):**

| Target vs local 125 tok/s | Required period | Verdict for current pipeline architecture |
|---------------------------|----------------:|-------------------------------------------|
| 80% (100 tok/s) | 10.0 ms | ❌ Not credibly — needs fixed tax + inflation ≤ 6 ms on top of a 3.9–5.1 ms compute floor; every serial cost (2 hops, sync, sample, dispatch) must total < 2 ms |
| 90% (112 tok/s) | 8.9 ms | ❌ No — below what the equal-layer compute floor + 2 hops can deliver |
| 95% (119 tok/s) | 8.4 ms | ❌ No |
| **50–60% (~70 tok/s)** | ~14 ms | ✅ Yes — bubble + sync + sampling + partial inflation removal, all identified and none requiring new hardware |

**Paths beyond ~60% for small models:**

| Alternative | Mechanism | Ceiling effect | Feasibility here |
|-------------|-----------|----------------|------------------|
| **Speculative pipeline** | Draft model (local, tiny) proposes k tokens; distributed pipeline verifies k in one pass — amortizes hops/sync/dispatch over k tokens | ×1.5–2.5 single-stream **[est]**, multiplies *with* all other fixes → can meet or exceed local | High — pipeline-friendly, no interconnect requirement; the single most valuable architectural addition |
| **Tensor parallel** | Split matmuls, allreduce per layer | Latency floor 2·n_layers·t_hop ≈ 13+ ms/token on 1 GbE — **worse than today's critical path** | ❌ Requires NVLink/RDMA-class interconnect; wrong for this cluster |
| **Hybrid (TP intra-node, PP inter-node)** | Standard datacenter recipe | Only helps nodes with multiple GPUs | N/A on this cluster today |
| **Continuous batching** | Overlap many streams | Aggregate throughput ×N, single-stream unchanged | Valuable if the product goal is serving, not latency |

**For ≥14B models (the actual value region):** the same fixed-tax reductions push efficiency to 80–90%+ of the cluster ceiling (§9.3), and "% of local" ceases to be meaningful because local is impossible. **Recommendation: adopt "% of own ceiling" (per `metric_validation.py`) and "largest servable model at usable TPS" as the primary success metrics; retire "% of local TinyLlama."**

---

## 14. Optimization Opportunity Matrix (no implementation)

Gains are for homelab TinyLlama single-stream unless noted; multiplicative composition per §12.2.

| Optimization | Max gain | Complexity | Risk | Addresses fundamental limit? |
|---|---|---|---|---|
| **Async scheduler / non-blocking dispatch** (verify & fix v2 client pipeline on homelab) | **+43%** (25.8→36.9) | Med (mostly built) | Ordering/KV correctness | No — removes pure overhead |
| **Local/GPU sampler + token-only return** | +15% | Med | Logits contract, sampler parity | No |
| **Sync relocation / overlap** (15.2 Option A/C) | +14% | Med | Buffer lifetime vs next decode | Wait itself is fundamental; placement isn't |
| **Graph capture / reuse (Metal cmd-buffer, CUDA Graphs)** | +10–20% **[est]** (attacks 9 ms endpoint inflation) | High (GGML-adjacent) | Backend divergence | No — submit overhead is implementation |
| **Bandwidth-proportional partition (planner v2 cost model)** | +23–30% of *ceiling*; smaller measured until other fixes land | Low | Memory-fit edge cases | No — allocation policy |
| **Persistent sessions / workers** | Startup 10–60 s → sub-second; 0 decode gain | Med | Lifecycle, multi-tenancy | No |
| **TensorProvider (11.7) — no materialization** | Startup only | High | llama.cpp integration | No |
| **Ownership redesign (GPU-resident hidden, D2D)** | ≤ +14% (subsumes sync win) | Very high | Cross-vendor impossible today (Task 15.3) | Partially fundamental (process isolation) |
| **Speculative decode (draft-verify over pipeline)** | **×1.5–2.5 [est]** | High | Acceptance rate, sampler equivalence | **Yes — the only lever that beats the serial-token law** |
| **Continuous batching** | ×N streams aggregate; 1-stream: 0 | High | Scheduler rewrite | Sidesteps it (amortization) |
| **FP16 hidden wire** | <0.1% | Low | Precision | No — transport already ~0 |
| **Binary protocol / zero-copy socket** | <0.1% | Low–Med | Protocol break | No |
| **Skip gather memcpy** | ~0% (0.003 ms) | Trivial | Buffer lifetime | No |
| **Faster interconnect (10 GbE/Thunderbolt)** | ~0% decode; prefill & cold-sync ×5–10 | Hardware | — | Helps prefill/startup only |

---

## 15. Recommended Roadmap

Ordered by (gain × confidence) / effort, respecting dependencies:

1. **R1 — Close the bubble on homelab.** Verify protocol-v2 flag engagement in the Task 14/16 traces; profile the remaining 11.7 ms of orchestrator work between waves; drive bubble to the RFC §28 <10% gate. *Expected: 25.8 → ~34–37 tok/s.*
2. **R2 — Sampling return path.** Keep sampling on final; cut logits access + framing so only the token id transits back. *+~15%.*
3. **R3 — Sync overlap.** `llama_synchronize` relocation or overlap with the dispatch gap (15.2 Options A/C) — cheaper after R1 makes the gap schedulable. *+~14%.*
4. **R4 — Endpoint compute study, then graph capture/reuse.** First a Task-15-style breakdown of entry/final inflation (submit vs kernels vs staging — currently inferential), then Metal/CUDA graph reuse. *Path to ~70–75 tok/s (55–60% of local).*
5. **R5 — Planner v2: bytes/bandwidth cost model** + "add node only if BW > cluster average" rule + smallest-share placement for slow nodes. *Raises every ceiling 23–30%.*
6. **R6 — Startup: persistent workers, cached graph reserve, event-driven READY, TensorProvider; fix qwen14b session_create.** *Session 10–60 s → ~1 s; unlocks ≥14B validation — the region where the architecture wins.*
7. **R7 — Speculative pipeline (research task first).** Draft-model-at-entry, k-token verify waves over the existing pipeline. *The only credible route past ~60% of local for small models; also multiplies large-model TPS.*

Explicitly deprioritized forever (this study's evidence): wire format, zero-copy, TCP tuning, hidden compression for decode — all bounded < 1%.

---

## 16. Explicit Non-Goals (confirmed)

No optimizations were implemented; no runtime, scheduler, GGML, transport, protocol, or llama.cpp changes were made. This document is analysis and modeling only; all roadmap items are recommendations for future tasks.

## 17. References

| Artifact | Path |
|----------|------|
| Token cost model (primary trace basis) | `docs/archive/TASK_16_END_TO_END_TOKEN_COST_MODEL.md` |
| Pipeline stall / bubble (Docker) | `docs/archive/TASK_12_PIPELINE_STALL_ANALYSIS_DOCKER.md` |
| Hidden transport / gather / sync / ownership | `docs/archive/TASK_15_1_HIDDEN_TRANSPORT_BREAKDOWN.md`, `TASK_15_1b_HIDDEN_GATHER_ROOT_CAUSE.md`, `TASK_15_2_GPU_SYNCHRONIZATION_STUDY.md`, `TASK_15_3_HIDDEN_OWNERSHIP_STUDY.md` |
| Protocol v2 design + phases | `docs/RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md`, `TASK_13_3…13_6` |
| Metrics discipline | `docs/PERFORMANCE_METRICS_SPEC.md`, `benchmarks/perf_trace/metric_validation.py` |
| Startup / benchmark data | `docs/archive/LAN_HOMELAB_BENCHMARK_REPORT_20260706.md`, `TASK_11_FULL_METRICS_AND_ARCHITECTURE_REPORT_20260706.md` |
| Architecture boundary | `docs/ORCHESTRATOR_RUNTIME_BOUNDARY.md`, `TASK_11_LAYER_FIRST_RUNTIME.md` |
