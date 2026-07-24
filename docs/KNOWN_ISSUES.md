# Known Issues

Running log of bugs found during manual/dashboard testing that aren't
tied to a specific gate report. Newest on top. Each entry: what broke,
how it was found, root cause (if known), status.

---

## OPEN

(none currently -- see FIXED and DISMISSED below.)

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
