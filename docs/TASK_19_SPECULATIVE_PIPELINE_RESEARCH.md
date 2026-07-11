# Task 19 — Speculative Pipeline (Research)

**Type:** Research / Architecture Design — ❌ no implementation in this task
**Status:** Planned
**Parent:** Research 17 (roadmap R7)
**Depends on:** conceptually none; economically after Task 17.1–17.4 (speculation multiplies the post-cleanup base rate)
**Expected gain:** ×1.5–2.5 single-stream [est] — the **only** lever that beats the serial-token law (Study §13)

---

## Why this is the endgame lever

Research 17 established: after the bubble, sync, sampling, and inflation fixes, single-stream pipeline physics caps at ~55–60% of local for small models. Speculative decoding amortizes **all** fixed per-token costs (hops, dispatch, sync, sampling round-trip) across k tokens per pipeline pass, because verifying k drafted tokens in one wave costs nearly the same as one token (decode is memory-bandwidth-bound; weights are read once per pass either way). The runtime already supports multi-token waves (prefill path) — a structural head start.

## Research questions

### A. Draft placement

| Option | Pros | Cons — to be quantified |
|--------|------|--------------------------|
| Draft on **final** node | co-located with sampler + accepted tokens; drafts during the return gap | drafted ids must reach entry → adds a hop before next wave |
| Draft on **entry** node | drafted ids feed dispatch directly | entry must learn accepted tokens (they already return there) |
| Draft on **orchestrator** | no worker changes | orchestrator re-enters data plane (Task 10 boundary violation?) |

Decide with a latency model per option using measured hop/dispatch numbers.

### B. Acceptance rate (offline, no runtime changes)

Measure draft acceptance offline with local llama.cpp speculative tooling: target models (TinyLlama, llama3-1b, qwen8b, qwen14b) × candidate drafts (smallest same-tokenizer family members) × k ∈ {2,4,8}. **Tokenizer compatibility matrix is a deliverable** — it constrains model catalog pairs.

### C. Protocol design (RFC-0014 draft)

- verify wave = k tokens, positions P+1..P+k; KV write semantics on partial acceptance (rollback vs provisional-write-then-truncate — interaction with per-stage KV caches is the hard problem)
- sampler contract: acceptance sampling preserving target-model distribution (standard speculative sampling), determinism per RFC-0013 §13
- failure/timeout semantics when a verify wave is rejected mid-pipeline
- WaveID semantics for draft vs verify waves; trace events for acceptance-rate observability

### D. Projected throughput model

Extend the Study §9 ceiling model: `T_k = (C·W + F) / E[accepted]` with measured acceptance; produce expected tok/s tables for the homelab cluster per (model, draft, k) and the break-even acceptance rate below which speculation loses.

## Deliverables

`docs/TASK_19_SPECULATIVE_PIPELINE_STUDY.md` containing: placement decision with latency model; offline acceptance-rate matrix; KV rollback design (chosen + rejected alternatives); RFC-0014 protocol sketch; projected gain tables; go/no-go recommendation with implementation phasing.

## Acceptance criteria

- Every research question A–D answered with numbers or an explicit measured-blocker.
- Acceptance rates from ≥ 3 target/draft pairs, ≥ 2 values of k.
- Projected model validated against at least one local speculative run (predicted vs measured speedup within ±25%).
- No runtime, protocol, GGML, or llama.cpp changes.

## Non-goals

Implementation; Medusa/EAGLE-style learned heads (require training — out of scope); tensor-parallel alternatives (ruled out on 1 GbE, Study §13).

## References

Study §12–13; `docs/RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md` §12–14 (wave semantics, autoregressive constraints); llama.cpp speculative example (offline measurement tooling).
