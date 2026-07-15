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
