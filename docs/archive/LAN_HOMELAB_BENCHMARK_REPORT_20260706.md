# LAN Homelab Benchmark Report

Date: 2026-07-06

**Run ID:** `20260706_200851`  
**Profile:** `homelab_full`  
**Mode:** `homelab` (physical LAN, no Docker)  
**Orchestrator:** `http://192.168.50.154:9000`  
**Artifacts:** `logs/benchmark/homelab_full_20260706_230800/`

Generated files:
- `logs/benchmark/homelab_full_20260706_230800/results.json`
- `logs/benchmark/homelab_full_20260706_230800/results.csv`
- `logs/benchmark/homelab_full_20260706_230800/report.md`
- `logs/benchmark/homelab_full_20260706_230800/report.html`

## Executive Summary

Homelab full benchmark ran **9 of 11** planned models over **~1h 23m** wall time, then was **stopped manually** after `qwen14b` failed at `session_create`. Models `qwen30b` and `gemma27b` were **skipped** — no point running heavier models until 14B session setup is fixed.

| Gate | Result |
|------|--------|
| Models attempted | 9 / 11 |
| Layout (`fits_cluster`) | **9/9 PASS** |
| Sync + coverage READY | **9/9 PASS** |
| Session create | **8/9 PASS** (`qwen14b` HTTP 500) |
| Warmup + measured generate (32 tok) | **8/8 PASS** (on models that reached session) |
| Skipped (manual stop) | `qwen30b`, `gemma27b` |

**Throughput (E2E, 16-token prompt + 32 measured tokens):**
- Average over 8 successful models: **12.04 tok/s**
- Best: **smollm2_1_7b** at **27.68 tok/s**
- Slowest (large): **qwen8b** at **1.50 tok/s**, **tinyllama** at **1.27 tok/s**

These numbers are end-to-end HTTP latency (prefill + 3-node pipeline decode + orchestrator overhead), not single-node peak decode.

## Cluster Topology

| Role | Node | Host | Hardware | RAM free/total | VRAM free/total |
|------|------|------|----------|----------------|-----------------|
| Orchestrator | homelab | 192.168.50.154:9000 | — | — | — |
| node-a | Mac | 192.168.50.42:9001 | Apple M3 Pro | 16.0 / 19.3 GB | 14.3 / 14.3 GB |
| node-b | Mac | 192.168.50.254:9002 | Apple M1 Pro | 13.2 / 17.2 GB | 11.5 / 11.5 GB |
| node-c | Windows | 192.168.50.51:9003 | RTX 4070 Ti | 20.5 / 33.5 GB | 11.6 / 12.9 GB |

**Combined cluster (at report time):** ~49.7 GB free RAM + ~37.4 GB free VRAM across 3 nodes.

## Benchmark Configuration

| Parameter | Value |
|-----------|-------|
| Models (profile order) | 11 catalog models |
| Cluster size | 3 |
| Prompt length | 16 |
| Warmup tokens | 4 |
| Generate tokens | 32 |
| Sync timeout | 7200s |
| Session create timeout | **1200s** (20 min) |
| `release_workers_between_models` | true |
| `purge_model_after_scenario` | true |
| `BENCHMARK_DOCKER` | 0 |

Software (benchmark driver host):
- node-agent: `87b8eca` (main)
- llama.cpp: `04e34f555` (v0.2.1 subtree)
- Build/backend: metal / Darwin arm64

## Result Matrix

| Model | Model ID | Layers | Status | Sync | Coverage | Session | Generate TPS | Prefill | Decode/token |
|-------|----------|-------:|--------|-----:|---------:|--------:|-------------:|--------:|-------------:|
| tinyllama | tinyllama-1.1b | 22 | **PASS** | 286s | READY | 10.3s | 1.27 | 1.46s | 282ms |
| llama3_1b | llama-3.2-1b | 16 | **PASS** | 851s | READY | 19.3s | 2.15 | 4.48s | 1040ms |
| qwen2_1_5b | qwen2.5-1.5b | 28 | **PASS** | 429s | READY | 14.7s | 2.84 | 1.20s | 385ms |
| gemma3_1b | gemma-3-1b | 26 | **PASS** | 1053s | READY | 10.3s | 19.62 | 575ms | 198ms |
| phi3_5 | phi-3.5-mini | 32 | **PASS** | 331s | READY | 15.7s | 18.14 | 696ms | 51ms |
| smollm2_1_7b | smollm2-1.7b | 24 | **PASS** | 116s | READY | 8.8s | **27.68** | 399ms | 39ms |
| deepseek_qwen_1_5b | deepseek-r1-distill-qwen-1.5b | 28 | **PASS** | 114s | READY | 9.3s | 23.16 | 510ms | 51ms |
| qwen8b | qwen3-8b | 36 | **PASS** | 465s | READY | 61.5s | 1.50 | 1.05s | 54ms |
| qwen14b | qwen3-14b | 40 | **FAIL** | 618s | READY | **500** | — | — | — |
| qwen30b | qwen3-30b | — | **SKIPPED** | — | — | — | — | — | — |
| gemma27b | gemma-3-27b | — | **SKIPPED** | — | — | — | — | — | — |

Download throughput during sync ranged **1.3–10.4 MiB/s** depending on model size and cache warmth.

## qwen14b Failure Analysis

`qwen3-14b` passed all infrastructure stages, then failed at runtime session setup:

| Stage | Result | Duration / detail |
|-------|--------|-------------------|
| layout | PASS | 40 layers, 7.92 GB weights, `fits_cluster=true` |
| synchronization | PASS | 443 blobs, 8.99 GB downloaded, 605s, 14.18 MiB/s |
| coverage | PASS | READY 40/40, `ready_time_ms` ≈ 618s |
| session_create | **FAIL** | HTTP **500**, 93.6s, no `session_id` |
| generate | not run | — |

### Not a 5-minute timeout

- Benchmark client timeout for this profile: **`session_create_timeout_s: 1200`** (20 minutes).
- Request completed in **93.6s** with HTTP **500** from orchestrator — not a client `timed out` (that would be `http_status: 0`).
- Orchestrator internal `wait_node_worker_ready` / `configure_node` limits are 300s per stage; a READY-timeout failure would typically run **~300s**, not ~94s.

### Likely failure point

HTTP 500 is returned when `setup_runtime_graph()` fails inside the orchestrator (worker configure, prepare, or READY polling). Sync/coverage are unrelated — layers were fully installed.

`layer_first_ok: false` in the trace is **not the root cause** — it reflects cumulative `materialization_count` across prior models in the same benchmark run. `qwen8b` passed with the same flag (`materialization_count=32`).

**Missing diagnostic:** orchestrator `error` string from the HTTP 500 body was not persisted in the trace (`StageRecord.to_dict()` omits `response`). Check orchestrator stderr on homelab around `2026-07-06T21:30–21:31 UTC` or re-run isolated `POST /session/create` for `qwen3-14b`.

## Performance Observations

1. **Small/medium models (≤2B)** on this 3-node LAN cluster: **2–28 tok/s** E2E — dominated by pipeline hops and prefill on entry node, not local 120+ tok/s single-node peaks.
2. **qwen8b (36 layers, 4.2 GB)** passed but slow: **1.5 tok/s**, session setup **61.5s** — memory pressure visible on M-series nodes.
3. **First-run sync** is expensive when blobs are cold (`llama3_1b` 851s, `gemma3_1b` 1053s); later models with warm cache are much faster (e.g. `deepseek_qwen_1_5b` 114s).
4. **Decode per token** on fast models: **39–54ms** (smollm2, phi3, deepseek) vs **282–1040ms** on slow ones (tinyllama, llama3_1b) — likely prefill-dominated E2E accounting on small models.

## Stop Decision

Benchmark was halted after `qwen14b` `session_create` failure while `qwen30b` had just started (no trace file produced). Rationale:

- 14B is the first model where session setup broke after successful sync.
- 30B and 27B are heavier; failures would likely reproduce the same class of runtime-graph error with much longer sync cost.
- Fix 14B first, then resume from `qwen14b` → `qwen30b` → `gemma27b`.

## Recommended Next Steps

1. **Reproduce qwen14b** in isolation: register → sync → `POST /session/create` with `n_ctx=512`, capture orchestrator `error` field.
2. **Save `response.error` in benchmark traces** for `session_create` failures.
3. **Check node worker states** during 14B configure — prior scenarios left workers in `STARTING` state on node-b/node-c at materialization snapshot.
4. After fix, resume benchmark with `--profile homelab_full` starting from `qwen14b` (or a dedicated `homelab_large` sub-profile).

## Conclusion

The homelab 3-node cluster successfully runs **8/8 models up to qwen8b** through full layer-first infrastructure: manifest, layout, LAN sync, coverage, session, and 32-token generation. The **14B boundary** is the current blocker at `session_create` (HTTP 500), not sync or planner fit. Heavier models were correctly skipped pending a fix.
