# Task 17.2 — Sampling & Token Return Path Reduction

**Type:** Investigation → Implementation
**Status:** Planned
**Parent:** Research 17 (roadmap R2)
**Depends on:** none hard; best measured after Task 17.1 lands
**Expected gain:** +~15% (5.18 ms → ≤ 1.5 ms on the serial path)

---

## Problem

Sampling already runs on the final worker (correct placement), but the `SAMPLER` span costs **5.18 ms/token** on homelab — 19% of the serial critical path — vs well under 1 ms for the same sampler chain in local llama.cpp. Research 17 §6.4 classifies ≥ 4 ms of it as implementation: logits materialization/access, sampler chain setup, and response framing back through the pipeline.

## Phase A — Attribute (no changes)

Instrument the final worker sampling window into sub-spans (Task 15.1-style):

1. logits sync/access (`llama_get_logits*` incl. any backend synchronize it triggers)
2. sampler chain execution (per-sampler if non-trivial)
3. KV/state bookkeeping attributed to the sampler window
4. token + metrics response framing and send (return path until entry/orchestrator receive)

**Exit:** ≥ 90% of the 5.18 ms attributed; identify whether a hidden GPU sync (logits analog of Task 15.1b) dominates.

## Phase B — Reduce

Candidate directions (choose from Phase A data, not all):

- single synchronize per token shared between logits access and any other result read (avoid double fence)
- persistent sampler chain (no per-token construction/reset beyond required state)
- return payload = token id + minimal metrics; move per-token metrics aggregation off the serial return path
- greedy/top-k fast path when the sampler config permits

**Contract:** sampler output must remain bit-identical for a fixed seed (determinism invariant, RFC-0013 §13).

## Acceptance criteria

| Gate | Threshold | Source |
|------|-----------|--------|
| Sampling sub-span attribution | ≥ 90% of SAMPLER span | new breakdown artifact |
| `SAMPLER` span (steady decode avg) | ≤ 1.5 ms | trace analysis |
| Serial critical path reduction | ≥ 3 ms vs pre-task baseline | `critical_path.json` |
| TPS uplift (same flags as baseline) | consistent with span reduction ±10% | `validation.json` |
| Sampler parity | same seed → same 32 tokens | parity test vs baseline |

## Non-goals

Moving sampling to another node; speculative decoding (Task 19); logits wire compression.

## References

Study §6.4, §12; `docs/archive/TASK_16_END_TO_END_TOKEN_COST_MODEL.md` §5–7; `docs/archive/TASK_15_1_HIDDEN_TRANSPORT_BREAKDOWN.md` (methodology).
