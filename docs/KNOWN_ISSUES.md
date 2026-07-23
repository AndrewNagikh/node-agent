# Known Issues

Running log of bugs found during manual/dashboard testing that aren't
tied to a specific gate report. Newest on top. Each entry: what broke,
how it was found, root cause (if known), status.

---

## OPEN

### Distributed generate loop never checks for EOG (end-of-turn) tokens
**Found:** 2026-07-24, dashboard testing after wiring up chat-template mode.
**Symptom:** Model answers correctly, then keeps going past its natural
end -- output contains literal special tokens (`<|eot_id|>`,
`<|start_header_id|>assistant<|end_header_id|>`) and hallucinated
follow-up turns, instead of stopping. Example (llama-3.2-3b, prompt
"привет"): `привет! как я могу помочь вам?<|eot_id|><|start_header_id|>assistant<|end_header_id|> Как я могу помочь вам?<|eot_id|>...`
**Root cause (confirmed):** Checked the whole distributed pipeline
(`orchestrator.cpp`, `node_agent.cpp`, `split_gen3_a/b/c.cpp`) for
`llama_vocab_is_eog()` -- it's called in exactly one place in the whole
tree, `e2e_common.h:843`, which is a local single-process test harness,
**not** the `/session/generate` production path. The real generate loop
always runs exactly `max_tokens` steps regardless of whether the model
produced an end-of-turn token. This was invisible before chat-template
mode (raw completions just rambled on with no defined stop point, which
read as "wrong but coherent" rather than an obvious bug) -- chat mode
made the model actually try to stop, which exposed the gap.
**Fix (not yet applied):** add an `is_eog` check to the entry-driven
generation loop in `split_gen3_a.cpp`, propagate an early-stop signal
through middle/final so they stop feeding further steps, and hide
special tokens on detokenize for the client-facing text.
**Related:** [chat-template fix](../llama.cpp/tools/distributed/node_agent.cpp) (52d1871ac) that surfaced this.
**Re-confirmed:** same `<|eot_id|>` leak visible again in a later
llama-3.3-70b session the same day (unrelated to the speculative-pair
finding below) -- this is not model- or size-specific, it's the shared
generate loop. **Top priority for next session** -- affects every model,
every prompt, through the dashboard.

### Decode speed anomaly: 2.1 tok/s on llama-3.2-3b (expected ~16-29)
**Found:** 2026-07-24, same dashboard session as above.
**Symptom:** `entry@node-a / middle@node-b / final@node-c`, prefill
1718ms (vs. 315ms on an earlier run of the same model/pipeline
placement in the same session), decode 2.1 tok/s (vs. established
baseline ~16.4 tok/s traced / ~29.5 tok/s production from the G3 soak).
**Status:** NOT reproduced/confirmed as a real regression. Checked
immediately after: no lingering/duplicate worker processes on node-a,
node-b/c/orchestrator all answered `/health` with normal RTT (78ms
Wi-Fi node-b, 15-16ms others). Could be a one-off (e.g. contention with
a local rebuild running moments earlier) rather than a systemic issue.
**Next step:** repeat the same prompt 2-3x and see if the number is
stable before treating it as a real bug. **Not re-tested this session**
-- got pulled into the 70B speculative-decoding investigation instead;
still open, carry over.

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

(none logged yet -- add here once the EOG-stop fix above lands, with
the commit hash and how it was verified.)
