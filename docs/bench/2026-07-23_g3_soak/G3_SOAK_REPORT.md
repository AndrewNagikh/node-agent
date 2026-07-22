# G3 Soak Report — 2026-07-23

Gate: `docs/FIRST_SHOWCASE_CRITERIA.md` G3 — "A 30-minute soak: >=20 sequential
create/generate/destroy cycles across >=3 models (incl. one 32B+ rung) with
zero manual node restarts, driven through the dashboard. NOT fault tolerance
-- just 'doesn't fall over while someone watches'."

**Verdict: STILL FAILS after one fix cycle — improved from 71% to 81% cycle
success, not zero-failure.** Two runs so far, same script, same protocol:

| Run | When | Cycles | Success rate | Notes |
|---|---|---|---|---|
| 1 | 2026-07-23, before fix | 107 | 71.0% | See "Run 1" section below. |
| 2 | 2026-07-23, after `689a554f6` (parallel+retry coverage poll) | 90 | 81.1% | See "Run 2" section below. Real improvement, not a full fix -- deeper cause found. |

Both runs: zero crashes, zero corrupted/inconsistent output, zero destroy
failures, every single failure the identical clean 503 `runtime coverage not
ready`. The fix applied between runs (parallelize + one retry on
`poll_installed_layers_from_nodes`, orchestrator-side) measurably helped but
did not close the gap — a **second, deeper** cause was found analyzing run 2
(see "Refined root cause" below) and is not yet fixed.

## Run 2 — after the parallelize+retry fix (2026-07-23)

| Model | Cycles | Success | Success rate | Avg decode tok/s | Avg TTFT (ms) | Run 1 rate |
|---|---|---|---|---|---|---|
| llama-3.2-3b | 23 | 22 | 95.7% | 29.8 | 229 | 100.0% |
| qwen3-14b | 23 | 21 | 91.3% | 15.6 | 431 | 66.7% |
| qwen2.5-32b | 22 | 11 | **50.0%** | 7.7 | 1534 | 44.4% |
| qwen3-30b | 22 | 19 | 86.4% | 24.6 | 1306 | 73.1% |
| **Total** | **90** | **73** | **81.1%** | | | **71.0%** |

qwen3-14b and qwen3-30b improved substantially (67%→91%, 73%→86%).
qwen2.5-32b barely moved (44%→50%) and remains the clear worst case — the
one model where the fix didn't meaningfully help, which is itself the key
diagnostic clue below. llama-3.2-3b, previously perfect, took one failure
this run (still 22/23) — noise, not a regression signal on its own.

### Refined root cause: `/installed-layers` does full-data checksum
verification on every single poll, not a cheap existence check

`node_agent.cpp:3249`'s `/installed-layers` handler calls
`layer_store::verify_layer()` / `verify_blob_tensor()` for **every** blob the
model has. `layer_store.cpp:218`:

```cpp
bool layer_store::verify_layer(const int32_t layer_index, const std::string & expected_checksum) const {
    const auto blob = get_layer(layer_index);
    ...
    std::vector<uint8_t> data;
    if (!load_layer(layer_index, data)) {   // reads the WHOLE blob off disk
        return false;
    }
    ...
    return checksum_matches(expected_checksum, data.data(), data.size(), ...);  // re-hashes it
}
```

This reads and re-hashes the **entire on-disk weight data** for the model on
every `/installed-layers` request — not a metadata/existence check. For
qwen2.5-32b (~18.5GB across 64 layers, the largest dense rung tested) this is
tens of GB of disk I/O + hashing per poll, on a node that in a tight soak
loop is very often *also* concurrently loading a different session's weights
from the same disk. This is a large enough cost on its own — independent of
network/concurrency timing — that a single retry with the same expensive
operation frequently doesn't help, which is exactly the residual pattern:
qwen2.5-32b (most data to verify) stayed the worst case almost unchanged,
while the other three models (less data, or in llama-3.2-3b's case much
less) improved sharply from parallelize+retry alone.

The orchestrator-side fix (parallelize + retry) was real and worth keeping,
but it was treating a symptom one layer removed from the actual cost driver.
**Not fixed yet.** Suggested direction: `/installed-layers` (or a coverage-
specific variant of it) should answer from cheap metadata (file exists +
expected size, or a cached checksum result refreshed on a background
interval / on-write rather than on every read) instead of re-verifying full
tensor content synchronously inside the session-create hot path. Full
checksum verification has real value (catching corruption) but belongs in a
periodic background sweep or an explicit `/verify` call, not in a path that
runs on every single session create.

## Run 1 — before the fix (original findings, preserved for reference)

**Verdict: FAILS as currently run — 71% cycle success rate, not zero-failure.**
The failure mode is not a crash, hang, or corruption; every failure is a
clean, well-formed 503 from a single, now-understood code path. Root cause
identified below with a concrete fix direction. Not yet fixed — this report
is the finding, not the patch.

## Protocol

- Driven purely through the orchestrator HTTP API (`/session/create` →
  `/session/generate` → `/session/destroy`), no dashboard UI available to
  drive directly in this environment — functionally identical call sequence.
- Zero manual node/process restarts during the run (the whole point of the
  gate). No intervention of any kind once started.
- 4 models rotated in fixed order, covering all four canonical showcase rungs
  (3B / 14B / 32B / 30B-MoE dense+MoE), all `coverage: READY` beforehand:
  `llama-3.2-3b`, `qwen3-14b`, `qwen2.5-32b`, `qwen3-30b`.
- Fixed prompt ("Explain in one sentence why the sky is blue."), 24 max
  tokens per generation.
- Budget: 30 minutes wall-clock, minimum 20 cycles, whichever is later, hard
  cutoff at budget+15min regardless.
- Actual run: **107 cycles in 1814s (~30.2 min)** — far exceeded the 20-cycle
  minimum in the time budget, since most cycles complete in 6-35s.
- Raw artifacts in this directory: `soak_test.sh` (the driver), `soak.log`
  (human-readable timeline), `results.jsonl` (one structured record/cycle).

## Headline numbers

| Model | Rung | Cycles | Success | Success rate | Avg decode tok/s | Avg TTFT (ms) |
|---|---|---|---|---|---|---|
| llama-3.2-3b | 3B | 27 | 27 | **100.0%** | 26.8 | 227 |
| qwen3-14b | 14B | 27 | 18 | 66.7% | 15.2 | 395 |
| qwen2.5-32b | 32B dense | 27 | 12 | 44.4% | 7.3 | 1548 |
| qwen3-30b | 30B-MoE | 26 | 19 | 73.1% | 22.3 | 1346 |
| **Total** | | **107** | **76** | **71.0%** | | |

- **Every** failure (31/31) was the identical error: `runtime coverage not
  ready`, HTTP 503. No crashes, no hangs, no timeouts on the client side, no
  corrupted or truncated generations, no `destroy` failures (0/76 successful
  sessions failed to destroy cleanly at the API level).
- **Generation correctness was perfect** across all 76 successful cycles:
  for each model, every single successful generation produced byte-identical
  output text (checked via prefix comparison) — no drift, no cross-model
  contamination, no corruption from rapid session churn.
- **llama-3.2-3b never failed once** (27/27). All failures landed on the
  three models that are pipeline-sharded across all 3 nodes; the 3B model's
  session-create is fast enough (~6s total cycle) that it rarely overlaps
  with another node under load.
- Failures cluster: once one big-model cycle fails, the next 1-2 often also
  fail (e.g. cycles 34→35→36 all failed back to back), then several cycles
  succeed cleanly before the next cluster. This is a load-correlated
  transient, not a permanent degradation — the system self-heals every time
  without intervention.

## Root cause (identified, not yet fixed)

`orchestrator.cpp:3212` (`/session/create` handler) calls
`poll_installed_layers_from_nodes()` on **every** session create — a live,
**synchronous, sequential** HTTP GET (`/installed-layers?model=X`) to every
*registered* node (not just the ones this model's layout actually uses),
3s connect / 10s read timeout each. Source, `orchestrator.cpp:1541-1563`:

```cpp
for (const auto & kv : nodes_copy) {
    if (!kv.second.online) continue;
    httplib::Client client(kv.second.host.c_str(), kv.second.http_port);
    client.set_connection_timeout(3, 0);
    client.set_read_timeout(10, 0);
    const auto result = client.Get(("/installed-layers?model=" + model_id).c_str());
    if (!result || result->status != 200) {
        continue;   // <-- silently drops this node's report
    }
    ...
}
```

If **any** node fails to answer within the window, it's silently dropped
from `online_nodes`, and `compute_runtime_coverage()` then reports the model
"not fully ready" even though its actual on-disk/in-memory blob state never
changed — the poll response was late, not the data wrong. A node is most
likely to answer this admin-plane request slowly while it is *itself* busy
inside a `/configure` call spawning a worker and loading tens of GB of
tensors for a **different, concurrent** session-create in the same soak
loop — exactly the condition a tight, back-to-back soak loop creates, and
exactly why the 3 multi-node models (long session-create times: 12-30s each,
see per-model cycle timing below) hit it far more than the 3B model
(session-create ~1-6s, node is barely ever "busy" when the next poll lands).

This fully explains the observed pattern: small/fast model never collides,
big/slow models collide often, failures cluster while a node is mid-load and
clear once it settles, and destroy — pure orchestrator-side bookkeeping,
touches no node — never fails.

**Not the same bug as the `/session/destroy` worker-leak gap** found earlier
this session (memory: `moe-graph-layer-range-gap.md`) — that one is about
a worker process outliving its session; this one is a false-negative on an
admin-plane liveness poll. Both are real, both are orchestrator-side, worth
fixing together.

**Suggested fix directions** (not implemented, for whoever picks this up):
poll only the nodes actually in this model's layout (not every registered
node); poll in parallel instead of sequentially so one slow node doesn't
serialize the whole check; retry once on a 503/timeout before failing the
session-create outright (a single retry would very likely absorb all 31
observed failures, given they clear within the next cycle 1-2 attempts
later); or make `/installed-layers` respond from cached bookkeeping instead
of live disk/mem state so it's cheap regardless of what else the node is
doing.

## Per-model cycle timing (successful cycles only, seconds, full
create→generate→destroy)

| Model | n | min | median | max |
|---|---|---|---|---|
| llama-3.2-3b | 27 | 5.7 | 6.8 | 11.2 |
| qwen3-14b | 18 | 13.6 | 15.8 | 27.3 |
| qwen2.5-32b | 12 | 26.2 | 28.3 | 38.4 |
| qwen3-30b | 19 | 19.6 | 22.4 | 34.9 |

## What this means for the showing

- **The core distributed-inference path is solid under repeated churn**:
  197 create/destroy cycles across both runs combined, zero crashes, zero
  corrupted output, perfect determinism per model. That's the load-bearing
  part of the story and it held up across two independent 30-minute runs.
- **G3 as literally worded ("zero manual restarts", implicitly ~100%
  success) is still not passable** after one real fix — 81% would still be
  visible and embarrassing live. The failure mode reproduced 31 times in run
  1 and 17 times in run 2, both with zero adversarial intent — this is not
  an edge case, it's the default behavior of a tight session-churn loop.
- The gap is narrower than it looked after run 1, and now points at a
  specific, well-understood cost center (full checksum re-verification on
  every coverage poll, see above) rather than a vague timing issue — that's
  real progress, but it's not fixed yet.
- Next step: fix `/installed-layers` to answer cheaply (metadata/cache)
  instead of re-reading and re-hashing full tensor data synchronously in the
  session-create path, then **run 3** with the same script to confirm.
  qwen2.5-32b (the model that barely moved between run 1 and run 2) is the
  one to watch — if this diagnosis is right, it should show the largest
  improvement of any model in the next run, since it has the most data to
  verify.

## Reproduction

```bash
docs/bench/2026-07-23_g3_soak/soak_test.sh       # run 1, pre-fix
docs/bench/2026-07-23_g3_soak/soak_test_v2.sh    # run 2, post orchestrator fix (689a554f6)
```

`soak_test_v2.sh` is identical to `soak_test.sh` except for its output file
names (`results_v2.jsonl` / `soak_v2.log`), so prior runs aren't overwritten.
Edit `MODELS`, `PROMPT`, `MAX_TOKENS`, `DURATION_SEC`, `MIN_CYCLES` at the
top of either script to adjust. Requires the orchestrator reachable at
`192.168.50.154:9000` (hardcoded — parameterize if run against a different
cluster) with the target models already `coverage: READY`.
