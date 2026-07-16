# Session 2026-07-14/15 — Homelab NODELAY validation, worker races, ops tooling

## What landed (all pushed: llama.cpp `feature/distributed-runtime`, node-agent `main`)

### Correctness fixes (llama.cpp)
| Commit | Fix |
|---|---|
| `0288cd67e` | `execv` PATH bug + fork-in-multithreaded-parent heap corruption in curl-subprocess fallback |
| `7a604965c` | Missing `FD_CLOEXEC` on sync-download pipe fds (cross-thread fd leak under concurrent fork) |
| `674923351` | Pipeline recovery never worked: `worker_gguf` missing from recovery configure |
| `5e66f36e0` | Phantom optional tensors (`.scale`/`.bias`) zero-filled in metadata-only loader → all-`<unk>` generation |
| `83e8a269b` | `DIST_RUNTIME_LAYER_FIRST` set too late (in `/configure`, after `/runtime/prepare` reads it) |
| `aac72a43c` | RESET-vs-decode data race on shared `llama_context` (macOS "Heap corruption detected" on final worker) |
| `e7127ff1d` | **Worker deadlock after client disconnect** — consumer parked in `queue.pop()` forever; every next generate hit "protocol negotiation failed" + ~30 s pipeline recovery. Added `close()` to both inbound queues + wakeups in all three workers |

### Ops tooling (node-agent `10f73fb`, llama.cpp `a55577670`)
- `GET /debug/log?lines=N` on node_agent and orchestrator — remote log tail, no more hopping between machines. Launch scripts tee stdout/stderr to `$MODELS_DIR/logs/{node_agent,orchestrator}.log` with rotation (20 MB × 3), still signal-addressable via exec.
- `POST /perf/trace/cleanup {"max_age_days":N}` on both — deletes raw trace `*.jsonl` older than N days.
- `scripts/cleanup_state.sh [--remote] [--dry-run] [--keep-runs N]` — prunes `logs/perf_trace/*_<ts>/` run dirs locally (freed ~12 GB / 38 dirs tonight) and fans out remote cleanup to all nodes from `nodes.conf`.

## Headline result — Task 17.1B homelab validation (details in TASK_17_1 doc)

Baseline 25.8 tok/s → now **~32 tok/s median** (runs: 22.6–34.8 wall) with recovery eliminated. Per-token period median 30.9 ms = compute Σ18.8 ms (entry 7.0 / middle 2.1 / final 5.1 / sampler 4.7) + residual ~12 ms (transport+queueing, ~39% of period). **Bubble gate (<10%) FAIL → Phase B scheduler work unblocked per the task's own gating.**

## Continuation plan (agreed order, next session)

1. **Tooling pre-step (small):** trace freshness validation (reject stale-mtime raw files), fix empty `node_id`/`component` on worker perf events, decide on non-env-gated trace propagation (orchestrator fanout currently requires orchestrator-side `DIST_PERF_TRACE=1`). Without this, 17.2/17.4 attribution runs measure garbage.
2. **17.2B — sampler/return path:** run homelab attribution with `DIST_RUNTIME_SAMPLER_SYNC_SPLIT=1` (instrumentation already landed), then reduce. Measured target: sampler 4.7 ms/token on node-b.
3. **17.1 Phase B continuation:** entry must ack enqueue immediately; COMPLETE must not gate the next wave (Docker Phase A finding — client pipeline not actually pipelined). Targets the ~12 ms residual.
4. **17.4 Phase A:** endpoint compute inflation research — entry 7.0 ms vs middle 2.1 ms for the same 8 layers.
5. **17.5:** planner v2 bandwidth-proportional placement; today's layout put the heaviest stage (final+sampler 9.8 ms) on the weakest node (node-b M1 Pro).

## Known loose ends / gotchas for next session

- **node-b and node-c must be updated + rebuilt + restarted** to pick up `e7127ff1d` (queue close) before any new measurements; node-a (this machine) already rebuilt.
- Uncommitted local changes, deliberately untouched (pre-existing, look like 17.2A/17.3-era instrumentation): `llama.cpp/src/llama-context.cpp`, `llama.cpp/tools/distributed/runtime_debug/perf_ggml.cpp` (EMBD_D2H/LLAMA_GET_EMBEDDINGS perf hooks), `benchmarks/perf_trace/{metric_validation,observability,postprocess,test_metric_validation}.py` (+227/−32), untracked `docs/TASK_15_1*.md`, `.cursor/`. Decide: commit or drop.
- Legacy GGUF materializer still produces wrong `token_embd.weight`/`output.weight` bytes — off the hot path since `83e8a269b`, but the fallback is broken; separate task candidate.
- Orchestrator (192.168.50.154) runs without `DIST_PERF_TRACE=1` → per-wave traces need manual `begin_decode` fanout to nodes (workaround used tonight), or fix item 1 above.
- Clock-safe analysis script for homelab traces: scratchpad `clock_safe_analysis.py` pattern (entry-clock periods + local `dur_us` only) — worth promoting into `benchmarks/perf_trace/` since cross-node timestamps are unusable on real hardware.

## Addendum (post-session review with user): node score is fake, role order inverted

User challenged why the strongest node (4070 Ti) never gets the heaviest work. Verified in code — two distinct defects:

1. **`dist_run_hardware_benchmark()` (node_benchmark.cpp) never measures anything.** Score = `cpu_cores*12 + gpu_memory_mb/256*40` — VRAM *size* plus core count, no bandwidth, no actual GPU execution. Formula reproduces all three logged scores exactly (node-a 2364.9 / node-c 2205.9 / node-b 1908.8). The real measured benchmark (`run_node_benchmark`) only runs when a local GGUF path is set — never in layer-first mode. Result: 4070 Ti (504 GB/s) scores below M3 Pro (150 GB/s) because 12 GB < 13.3 GB VRAM.
2. **Role order assumes entry is heaviest.** Layout assigns descending score to entry→middle→final, but Research 17 (and tonight's traces: final+sampler 9.8 ms vs middle 2.1 ms) shows final is the heaviest per-token stage. Even with correct scores the heaviest role would land on the weakest node.

Interim fix (cheap, before full 17.5): (a) hardware-only score should estimate decode capability from memory *bandwidth* class, or better, run a tiny bandwidth-bound GPU probe (no model needed — time a large `ggml_mul_mat` / memcpy on the backend); (b) assign final role to the highest-score node. Full fix remains 17.5 (bytes ∝ BW_eff cost model + endpoint extras).

User's design intent, confirmed: strongest nodes take the heaviest compute; weak nodes contribute memory for fit. Current code does neither.

## Continuation session (2026-07-15, later): score/role fix + tooling landed, new stall found

### Landed and verified live on the cluster

| Commit | Fix |
|---|---|
| `876275c0c` (llama.cpp) / `149c5ab` | Bandwidth-probe node score (256 MiB f16 matvec) replaces the fake `cores*12+VRAM/256*40` formula; final role assigned to strongest node instead of weakest |
| `d4a6ac8f3` (llama.cpp) / `75abeff` | Perf-trace tooling gaps: request-driven trace enable on `/session/generate` (`perf_trace:true` body), worker `DIST_NODE_ID`/`DIST_PERF_COMPONENT` set unconditionally at spawn, trace-id collision fix (embeds process start epoch), `mtime_unix` in `/perf/trace/list`, stale-file filtering in `collect_traces` (`min_mtime_unix`) |

Both commits authored in an earlier parallel session (`3b1c7034c`, `64e2486` — GATHER decode-path decomposition + clock-skew fallback for cross-node critical path) were reviewed: GATHER instrumentation is solid end-to-end (event names match the analyzer exactly). The clock-skew fallback's `serial_span_sum` level is currently **unreachable** on real traces — `_hop_ms(..., "entry", "ab")` never matches because no code emits a `HIDDEN_TRANSFER` event with `link="ab"` from the entry side (only `middle`->`"bc"` exists in `split_gen3_b.cpp`); on skew it silently degrades one level further to `compute_sum`. Not a regression, just an unfinished piece — worth an `ab`-side `perf_emit_hidden_transfer` call if anyone works the residual further.

**Verified live after rebuild+restart on all 3 nodes:**
- Real scores: node-c (4070 Ti) **1929.0** (461 GB/s), node-b (M1 Pro) **575.4** (137.6 GB/s), node-a (M3 Pro) **509.8** (122.0 GB/s) — matches real hardware bandwidth ranking (previously node-a was ranked highest and node-b lowest, backwards).
- Role order followed: `node-b=entry(0-4), node-a=middle(4-8), node-c=final(8-22)` — final correctly on the strongest node, and it received a proportionally large layer share (14/22) per the existing score-proportional layer-count allocation.
- Trace collection: stale-file filtering confirmed working (`(stale files before run start filtered)`, 10 fresh files vs 189-191 before); a follow-up run got a full `PASS` validation with real per-stage spans (previously blocked by the trace-id/tooling gaps).

### New, unresolved finding: final-stage compute on node-c is unstable, not just "not proportionally fast"

Two consecutive identical runs gave wildly different wall TPS: **19.3 tok/s** then **1.87 tok/s** (516 ms/token), neither run flagging `first_error`/`recovered_pipeline`. Per-stage compute from the working trace (run 2): **middle (node-a, 4 layers) avg 6.8 ms**, **final (node-c, 14 layers) avg 40.5 ms, max 164.1 ms**. Naive bandwidth-ratio expectation for final would be roughly the same order as middle (14 layers / 461 GB/s vs 4 layers / 122 GB/s -> comparable), not 6-24x higher with 4x run-to-run variance.

Working hypothesis (not yet confirmed): the bandwidth probe (single large sustained matvec, 256 MiB) does not represent real batch=1, small-model decode cost on CUDA -- per-layer kernel-launch/sync overhead, driver/Windows scheduling latency, or thermal/PCIe effects could dominate over raw bandwidth at this scale. Also noted in passing: entry-stage (`node-b`) compute spans (`ENTRY_RECEIVE`/`ENTRY_COMPUTE_END`) were absent from the collected trace file even though `CLIENT_*` (client decode-loop) events were present -- the actual `split_gen3_a` worker's own perf events did not appear in what got collected, a second, separate trace-attribution gap worth investigating alongside the compute-variance question.

**Not root-caused tonight.** Candidate next steps: (1) run several repeated generates against the already-synced session to separate transient variance from a stable regression; (2) instrument node-c's final-stage compute at finer granularity (per-layer, not just per-wave) to see if the 164 ms spikes correlate with specific layers/GC/thermal events; (3) check Windows Defender/firewall overhead on the newly-downloaded blob files; (4) sanity-check the bandwidth probe against a second, more decode-realistic probe (many small matvecs matching actual layer shapes, not one big sustained one) before trusting it as the layout cost signal for 17.5.

## Continuation session (2026-07-15, later still): root cause found for part of the node-c stall — GPU stuck at P8 during pipeline decode

### Root cause: GPU never leaves idle P-state during the real pipeline, even though it boosts fine in isolation

Isolated local rebenchmark (`node_agent.exe --model worker_PIPELINE_STAGE.gguf --rebenchmark`, no orchestrator, back-to-back decode calls with no network wait) on node-c (RTX 4070 Ti): `decode_tps=397.7 prefill_tps=10187.4 load_ms=380 score=3334.6` — about 2.5 ms/token. Sampling `nvidia-smi` in parallel showed the GPU ramping **P8 (210 MHz) → P2 (2775-2880 MHz) within ~1 second** of load starting and holding boost for the whole run. Temp stayed at 34°C — no thermal throttle.

Then ran the same model through the real 3-node pipeline (`benchmark_runner.py --mode runtime-only --model tinyllama --generations 5 --profile-runtime`, against the live orchestrator at `192.168.50.154:9000`, node-b=entry/node-a=middle/node-c=final as already established): wall TPS **30.7-40.1 tok/s** (~25-33 ms/token) — matching the originally reported ~36 ms/token. `nvidia-smi` sampled in parallel for the full ~70s run showed the GPU parked at **P8/210MHz for the entire pipeline run** (util oscillating 32-54% the whole time from bursty compute+network activity), only jumping to P2 in the last few seconds *after* generation 5 completed, during report writeout.

Explanation: in the real pipeline each token's compute on node-c is a short burst gated by a network round-trip to node-a/node-b for the previous hop's hidden state. Those bursts are too short and too sparse for the driver's boost heuristic to consider the GPU "busy enough" to ramp clocks — unlike the isolated benchmark, which issues decode calls back-to-back with no idle gaps. NVCP "Prefer maximum performance" (already set globally and per-exe for `node_agent.exe`/`split_gen3_c.exe` per user) only biases the boost algorithm's *preference*; it does not override the physical ramp-up latency (~50-100ms of sustained load needed) that these bursts never sustain — consistent with the previously observed "TPS went up 2.6x, not radically" from that setting alone.

### Fix tested: `nvidia-smi -lgc` (lock GPU clocks) — partial but real and stable win

Locked clocks with an elevated `nvidia-smi -lgc 2775,2775` (2775 MHz = the sustained boost clock observed in the isolated test; requires admin, confirmed "current user does not have permission" when unelevated). Re-ran the identical live 5-generation pipeline benchmark with the lock held and `nvidia-smi` sampled throughout:

- Clock log confirms **2775 MHz held for the entire run**, no drop to P8 at any point.
- Wall TPS: **43.4-45.2 tok/s** (up from 30.7-40.1) — a 15-25% improvement, and the run-to-run spread collapsed from ~±10 tok/s to ~±2 tok/s.
- Idle power draw rose from ~25W to ~38-39W while the lock is held (GPU no longer parks at P8 between requests) — a real tradeoff, not free.
- Even with the lock, pipeline decode (~22 ms/token) is still ~9x slower than the isolated compute-only figure (2.5 ms/token) — P8-throttle explains a real, fixed slice of the gap (and likely most of the run-to-run *instability*, since whether clocks happen to catch a boost window before this fix was apparently timing-dependent), but the bulk of the remaining 9x gap is something else, most likely cross-node network/sync round-trip cost per token (matches the still-open GATHER/HIDDEN_TRANSFER instrumentation gaps noted above) rather than GPU compute or clock behavior.

**Landed**: [run-agent.ps1](../run-agent.ps1) now locks the GPU boost clock (`nvidia-smi -lgc <max>,<max>`, using the driver-reported `clocks.max.sm`) before launching `node_agent.exe` whenever a CUDA build is detected (`ggml-cuda.dll` present) and the script is running elevated, resetting it (`-rgc`) in a `finally` block on exit. Non-fatal and silent no-op if `nvidia-smi` is absent (non-CUDA nodes) or not elevated (warns once with the remediation instead of failing the launch), matching the existing `Ensure-FirewallRules` pattern. Not yet load-bearing: this needs the node to be launched as Administrator at least once to take effect; unelevated launches still get the old idle-clock behavior with a warning.

**Still open**: the remaining ~9x gap between isolated and pipeline decode speed. Next step is probably per-hop network/serialization timing (candidate (2) above, or the still-unreachable `ab`-side `HIDDEN_TRANSFER` gap from the earlier session) rather than anything GPU-clock related — that lever has now been pulled.

## Continuation session (2026-07-15, evening): 40 tok/s verified, ab-hop root cause found and fixed

### Headline: GPU clock lock verified in the full pipeline

Ran a full 3-node benchmark with the clock lock active on node-c: **40.1 tok/s wall / 45.0 tok/s decode**, no errors, PASS validation. Up from 25.8 baseline (+55%), 66% of the Docker ceiling (60.7 tok/s). Client-side wait breakdown confirms Task 17.1 Phase B (ack_wait/complete_wait not overlapping waves) is a non-issue on this runtime path today: `ack_wait` avg 0.06 ms, `send` 0.11 ms, `complete_wait` 0.07 ms -- essentially zero. All remaining time is `token_wait` (genuine pipeline compute+network), which is the correct/healthy state per the task's own framing.

### ab-hop instrumentation: added, then found genuinely broken end-to-end

Added the missing entry-side `perf_emit_hidden_transfer(..., "ab", ...)` call (llama.cpp `d9532c3c5`), mirroring `split_gen3_b.cpp`'s existing `"bc"` emission -- both entry send paths (`forward_to_peer` legacy, `send_hidden_to_b_only` queued). Built clean, pushed, deployed to all 3 nodes.

Verified on the next run: **the ab hop still didn't appear** -- and neither did *any* of split_gen3_a's own worker events (`ENTRY_RECEIVE`/`ENTRY_COMPUTE_END`), even though split_gen3_b (middle, node-a) and split_gen3_c (final, node-c) both wrote full traces in the same run. Root-caused via code read, not guessing:

- `perf_trace_enabled()` (`runtime_debug/perf_trace.cpp`) reads `DIST_PERF_TRACE` from the environment exactly once per process and caches it forever (`g_cfg_loaded`); `write_event()` gates purely on that cached flag.
- Worker processes (`split_gen3_a/b/c`) are forked during `/session/create` (`setup_runtime_graph` -> `configure_node` -> `perf_attach_trace`), which runs **before** any `/session/generate` call exists to request tracing. Env vars set in the orchestrator/node_agent *after* a child has already forked never reach that child -- this is a hard OS constraint, not a caching bug that a reload call could fix.
- `perf_attach_trace()` was already correctly gated on the orchestrator's own `perf_trace_enabled()` + an active trace context, but nothing ever populated either before `/session/create`'s pipeline setup ran, because `/session/create`'s handler never read a `perf_trace` field from its own request body (unlike `/session/generate`, fixed earlier today).
- Net effect: whether a given session's workers got traced depended entirely on whichever *stale* enabled-state each node's long-lived `node_agent` process happened to already be sitting in from earlier, unrelated interactions that session -- explaining why middle/final "worked" and entry didn't in the same run: pure accident of interaction history, not anything role-specific.

**Fix** (llama.cpp `506dff53f`, node-agent `a6f1652`): mirror the `/session/generate` fix on `/session/create` -- read `perf_trace` from the body, `dist_set_env("DIST_PERF_TRACE","1")` + `perf_trace_reload_config()` before `setup_runtime_graph()` runs, so `perf_attach_trace()` has a real context to hand every worker at spawn. `benchmark_runner.py`'s session_create call now also carries `perf_trace:true` when `DIST_PERF_TRACE` is set, matching `generate_request_body()`.

**Not yet verified live** — this fix only touches `orchestrator.cpp` (no node-side changes needed; nodes are already on `d9532c3c5`). **Orchestrator not yet updated/restarted as of session end.**

## Next session starting point

1. Update + rebuild + restart the orchestrator only (192.168.50.154) — nodes are already current.
2. Re-run the benchmark and confirm entry's own worker events (`ENTRY_RECEIVE`/`ENTRY_COMPUTE_END`/`HIDDEN_TRANSFER(link=ab)`) finally appear alongside middle/final.
3. With all three hops now measurable, get the real ab vs bc timing split and decide the next lever (network/serialization reduction vs 17.4 endpoint inflation vs 17.5 full cost model) from actual data instead of estimates.
4. Architecture note from today's discussion (see `docs/PROJECT_VISION_DISTRIBUTED_NETWORK.md`, already adopted): full tensor parallelism (splitting entry/final compute itself across nodes) is physically ruled out on LAN (§13 latency floor). "Hundreds of nodes with no strong ones" is answered by the existing dynamic per-session role assignment (already handles homogeneous pools fine) plus Task 20.x (fault tolerance/elastic membership, not started) and Task 21 (multi-tenant batching, not started) -- not by restructuring the pipeline. MoE expert-parallel routing is the one genuinely roleless execution model, and it's unbuilt.

## Session 2026-07-16: trace plumbing fully fixed, residual attributed to Wi-Fi return path

### Fixes landed (all pushed)

| Commit | Fix |
|---|---|
| `296710c27` | Two node_agent trace-enable sites missing `perf_trace_reload_config()` (incl. `perf_consume_trace_from_body` used by `/configure`) -- workers spawned with tracing off despite the orchestrator attaching trace context |
| `ab732467f` | node_agent exits on first failed orchestrator registration instead of retrying (heartbeat never got a chance to run); now retries 15x/2s |
| `df4afc4eb` | **The big one: perf-trace output file opened once per process and never reopened on context change.** A worker's first write usually lands during prefill, so its handle stuck to the ttft subdir of whatever trace was current, and every later decode event of every later trace was appended there forever. This single bug explains ALL "missing" worker events across two days: entry's 994 decode events (incl. 80 ab HIDDEN_TRANSFERs) physically existed on disk, misfiled |
| `141ae8d62` | Revert of `d9532c3c5` (duplicate ab emission): the ab HIDDEN_TRANSFER was always emitted by `hidden_pack_emit_breakdown_spans`; it was only ever misfiled, never missing |

Diagnostic detour also caught a real ops hazard twice: duplicate `node_agent` processes both binding :9001 (SO_REUSE lets both listen; requests randomly hit stale code). Worth a startup guard eventually.

### Full period accounting -- finally closes (median, 32-token generate, entry=node-b/middle=node-a/final=node-c 4/4/14)

| Component | ms |
|---|---|
| entry compute + GATHER (node-b) | 5.0 + 2.2 |
| middle queue+compute+send (node-a) | 6.9 |
| final queue+compute+sampler (node-c) | 8.8 |
| **token return path final->middle->entry->client** | **13.7** |
| Total | ~36.6 vs 37.3 measured ✓ |

Method: node_agent and the entry worker share node-b's monotonic clock (same box), so `ENTRY_SEND_END -> CLIENT_TOKEN_WAIT_END` bounds the remote round trip exactly; middle/final internals measured on their own clocks; the difference is the uninstrumented return relay.

**Root cause of the return-path cost: Wi-Fi.** Measured RTTs: a<->b 5.9 ms avg with **10% packet loss**, a<->c 6.9 ms avg with spikes to 27 ms. Two serial return hops x ~3 ms one-way + thread wakeups = 13.7 ms; TCP retransmits from the packet loss explain the 358 ms period spikes. The forward hidden-state path is pipelined (overlapped with compute) and doesn't hurt; the return path is serial and fully exposed. Socket sends themselves are 0.06-0.23 ms -- NODELAY did its job; this is physical airtime latency, not a protocol defect.

### Decision (user): cables are not an option -> Task 19 (speculative pipeline) is the path

Per `docs/TASK_19_SPECULATIVE_PIPELINE_RESEARCH.md`, speculation amortizes the entire fixed per-token cost (now precisely measured: ~13.7 ms return + queue overheads) across k tokens per verify wave. Today's numbers feed directly into research questions A (placement latency model) and D (projected throughput: `T_k = (C*W + F)/E[accepted]` with F ~= 14-16 ms measured). Question B (offline acceptance rates) is fully local and can start immediately. A direct final->entry return connection (saves one Wi-Fi hop, ~3-6 ms) should be considered as part of the RFC-0014 return-path design rather than as a separate interim change.

Deploy note: nodes currently run the `df4afc4eb` build (harmless duplicate ab emission per token); `141ae8d62` cleans it up at the next convenient restart. Analyzers filtering HIDDEN_TRANSFER should prefer the event whose dur_us != attrs.send_us until then.
