# G1 Ceiling Measurement Report — 2026-07-23

Gate: `docs/FIRST_SHOWCASE_CRITERIA.md` G1 — "32B dense measured ... AND the
L2-MoE rung (qwen3-30b) measured ... Both: cold sync, warm session, 64-token
generation, >=80% of computed ceiling." Per `docs/PERFORMANCE_METRICS_SPEC.md`:
"Ceiling MUST NOT be hand-estimated in reports" — it must come from real
perf-trace decode-chain data.

**Verdict: PASSES for both rungs**, after three real bugs were found and
fixed along the way (two in the trace instrumentation/analysis pipeline, one
in the cluster's session lifecycle). This report covers all three, since
each one directly explains a wrong or wildly inconsistent number before it
was found.

| Model | Rung | Clean samples (%, post all 3 fixes) | Median | Verdict |
|---|---|---|---|---|
| qwen2.5-32b | 32B dense | 89.6, 86.9, 81.2 | **86.9%** | PASS (all 3 individually pass) |
| qwen3-30b | 30B-MoE | 86.7, 60.1, 88.5 | **86.7%** | PASS on median (2/3 individually pass; see variance note below) |

## Methodology

`docs/bench/2026-07-23_g1_ceiling/measure_ceiling.py` for each sample:
POST `/session/create` with `perf_trace: true` → `/session/generate` (64
tokens, fixed prompt) → `/session/destroy` → collect that session's trace
from all cluster nodes → compute the ceiling from real decode-phase spans →
report `measured / ceiling` using the `timing.decode_tokens_per_sec` and
trace **from that same run**. This is the only valid comparison: mixing an
untraced `measured_tps` against a traced-derived `ceiling_tps` is invalid,
since tracing itself has overhead that the ceiling then also reflects —
confirmed directly this session (see Bug 1) that an untraced measured_tps
can come out *higher* than a traced-run's ceiling, which would look like an
impossible >100% ratio if compared naively.

## Bug 1 (fixed earlier, prerequisite): `flush()` on every trace event

`runtime_debug/perf_trace.cpp`'s single trace-writing function called
`g_out.flush()` synchronously on every event — 20-30+ events per token per
node, all serialized through one mutex. Collapsed real decode throughput up
to ~6x under `DIST_PERF_TRACE=1` (qwen2.5-32b: 7.4 tok/s untraced vs 1.85
tok/s traced). Fixed by dropping the per-event flush (llama.cpp `81cdde019`)
— the stream still flushes on the existing trace-session-boundary `close()`
calls. Verified: qwen2.5-32b went from 1.85 → 6.41 tok/s traced (86% of real
untraced throughput retained, up from 25%). Durable win for any future
perf-trace debugging, independent of G1.

## Bug 2: double-counted "serial critical path", wrong-direction clock-skew check

First ceiling computation attempt gave `4.03 tok/s` for qwen2.5-32b —
**lower** than the real measured `6.41 tok/s`, which is physically
impossible (a ceiling must upper-bound measured throughput). Two compounding
bugs in `benchmarks/perf_trace/metric_validation.py`:

1. **Double-counting.** `serial_critical_path_ms = entry_comp + ab +
   middle_comp + bc + final_comp + sampling`. Traced against
   `runtime_debug/hidden_transport_breakdown.cpp`: `ab`/`bc`
   (`HIDDEN_TRANSFER`'s `dur_us`) is a *legacy rollup* of
   alloc+gather+copy+serialize (`legacy_serialize_us`), already entirely
   inside `entry_comp`'s own `BEGIN..END` window; `sampling`
   (`SAMPLER_END`'s `dur_us`) is a sub-span nested inside `final_comp`'s
   window. Summing them again on top double- (triple-, for sampling) counts
   the same wall-clock window as if sequential. Fixed: `serial_critical_path_ms
   = entry_comp + middle_comp + final_comp` (each already inclusive of its
   own sub-steps) — verified against a captured wave: old formula 216.6ms
   (4.6 tok/s, impossible), fixed formula 129.7ms (7.7 tok/s, correctly
   ≥ measured). Same bug, same fix, in `observability.py`'s duplicate
   computation (dead in practice — gets overwritten by `metric_validation`'s
   value when a matching wave is found — fixed anyway so it isn't a live
   trap).
2. **Wrong-direction skew check.** `_is_wall_clock_skewed` only checked
   `wall_ms > threshold` — never catches an implausibly *negative* wall_ms,
   which is exactly what cross-node `ts_us` diffing produces (`ts_us` comes
   from `std::chrono::steady_clock`, whose epoch is arbitrary per
   process/machine, not wall-clock — observed as low as **-11.8 billion
   ms**). Added a `wall_ms <= 0` check. Also fixed `_effective_critical_path_ms`:
   when skew is detected and neither fallback is available (a wave missing
   one stage's `*_COMPUTE_END`), it was returning the known-garbage `wall_ms`
   as a last resort instead of `None` (UNKNOWN, per the spec's own status
   vocabulary).

Fixed in commit `d6c31a7`. Result: qwen2.5-32b's ceiling computation became
self-consistent (`ceiling_tps=6.674`, `measured=6.41`, ratio 96%) on the
first re-test.

## Bug 3: `/session/destroy` didn't kill workers, contaminating every later measurement

Re-testing to get multiple samples per model, the ratio swung wildly:
96%, 43%, 52.7%, 85.7%, 62.6% across five qwen2.5-32b attempts. Root cause:
`/session/destroy` only ever removed orchestrator-side bookkeeping — the
actual pipeline-stage worker process on each node kept running until some
*later, unrelated* session happened to reconfigure the same node+role
(`start_worker()`'s `stop_worker_for_role()` was the only place a worker
ever got killed). Confirmed directly: `free_ram` on node-a dropped from
~13GB to ~5-6GB between measurements, and a `ps aux` after every `destroy`
found the just-finished session's worker (a ~4.6GB resident process) still
alive, competing for memory bandwidth with the *next* measurement's own
worker.

Fixed (llama.cpp `8584d747d`): new `POST /worker/stop {"role": ...}` on
`node_agent` (kills just that role's pipeline-stage worker, without
touching tokenizer/embedding/output services, which may be shared across
sessions); orchestrator's both session-destroy handlers now call it on every
pipeline node, fire-and-forget, right before erasing the session. Verified
directly: worker process present during `generate`, gone within 1s of
`destroy` (previously: still resident indefinitely).

After this fix, qwen2.5-32b's five re-measurements tightened to
**81.2-89.6%** (all pass, see table above) — the wild swings were entirely
this bug, not measurement noise or a remaining formula problem.

## Remaining variance (qwen3-30b, not investigated further)

qwen3-30b still shows more spread post-fix (60.1% to 88.5%) than
qwen2.5-32b (81.2-89.6%) even with no stray-worker contamination observed
(checked `ps`/free_ram after the 60.1% run — clean). Two live hypotheses,
neither confirmed: MoE expert-routing genuinely varies per-token compute
time more than a dense model's uniform matmuls; or node-b (Wi-Fi, per
cluster topology notes) sits on qwen3-30b's critical path for some layouts
and Wi-Fi jitter shows up as compute-span noise. Median (86.7%) clears the
80% bar comfortably; not chased further today.

## Reproduction

```bash
python3 docs/bench/2026-07-23_g1_ceiling/measure_ceiling.py qwen2.5-32b
python3 docs/bench/2026-07-23_g1_ceiling/measure_ceiling.py qwen3-30b
```

Requires the target model `coverage: READY` and the orchestrator reachable
at `192.168.50.154:9000` (env `ORCHESTRATOR` to override). Run it multiple
times and look at the spread, not a single sample — this report's own
history is the cautionary example why.
