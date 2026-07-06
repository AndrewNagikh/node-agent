# Task 11 Endpoint Runtime — Docker Benchmark Report

**Run ID:** `20260706_093352`  
**Profile:** `task11_docker`  
**Mode:** `task11`  
**Cluster:** Docker `orchestrator + node-a/node-b/node-c`  
**Runtime:** Layer-first runtime with explicit advertised service endpoints  
**Artifacts:** `logs/benchmark/task11_endpoint_docker_20260706/`

## Executive Summary

The full 8-model Task 11 Docker benchmark completed with runner exit code `0`.

The new architecture now consistently passes the infrastructure part of the benchmark across all small architecture families in the matrix:

- Manifest and architecture detection succeeded for all 8 models.
- Layout succeeded for all 8 models (`fits_cluster=true`).
- Synchronization reached `coverage READY` for all 8 models.
- Layer-first runtime gate stayed clean: `materialization_count=0` for every scenario.
- Runtime graph now serializes explicit `endpoint` objects and sessions show advertised service endpoints instead of implicit localhost routing.

However, this run does **not** yet produce valid 32-token throughput numbers. Runtime generation is still unstable in the multi-model benchmark:

- 6/8 models created a session.
- 0/8 models completed the measured `generate` stage.
- 5/8 models reached warmup/generate and then failed with `503`.
- 2/8 models failed at `session_create` after `node-c` became unavailable during larger-model runtime setup.

This means the endpoint fix is validated at the architecture/routing level, but benchmark TPS should be treated as **not measured**, not as true `0 tok/s`.

## Environment

| Item | Value |
|------|-------|
| Orchestrator | `http://127.0.0.1:9000` |
| Nodes | `node-a`, `node-b`, `node-c` |
| Docker node memory | ~7.65 GB RAM per node |
| Cluster memory at report start | 20.62 GB free / 22.96 GB total |
| GPU / VRAM | none / 0 GB |
| Prompt length | 16 |
| Generate tokens | 32 |
| Sync timeout | 3600s |
| Restart policy | Docker nodes restarted between models |

Note: generated report metadata says `backend=metal` because the benchmark driver runs on macOS. The measured distributed cluster is Docker CPU-only (`backend=cpu` in node snapshots).

## Model Matrix Result

| Model | Arch | Layers | Layout | Coverage | Session | Warmup | Generate | Main Runtime Note |
|------|------|--------|--------|----------|---------|--------|----------|-------------------|
| `tinyllama` | llama | 22 | PASS | READY 22/22 | 200 | 200, 4 tok @ 5.48 tok/s | 503 | `remote embedding service failed` |
| `llama3_1b` | llama | 16 | PASS | READY 16/16 | 200 | 503 | 503 | `tokenizer service failed on node-b` |
| `qwen2_1_5b` | qwen2 | 28 | PASS | READY 28/28 | 200 | 200, 4 tok @ 2.72 tok/s | 503 | `remote embedding service failed` |
| `gemma3_1b` | gemma3 | 26 | PASS | READY 26/26 | 200 | 503 | 503 | embedding/tokenizer failures, `node-c` refused cleanup |
| `phi3_5` | phi3 | 32 | PASS | READY 32/32 | 500 | not run | not run | `node-c` unavailable during/after session setup |
| `smollm2_1_7b` | llama | 24 | PASS | READY 24/24 | 200 | 503 | 503 | `tokenizer service failed on node-b` |
| `deepseek_qwen_1_5b` | qwen2 | 28 | PASS | READY 28/28 | 200 | 200, 4 tok @ 2.18 tok/s | 503 | `remote embedding service failed` |
| `qwen8b` | qwen3 | 36 | PASS | READY 36/36 | 500 | not run | not run | `node-c` unavailable during/after session setup |

## Infra Metrics

| Model | Planner | Placements | Install | Download | Retries | Coverage | Materialization |
|------|---------|------------|---------|----------|---------|----------|-----------------|
| `tinyllama` | 51 ms | 22 | 46.0 s | 0.62 GiB | 0 | 624 ms | 0 |
| `llama3_1b` | 46 ms | 16 | 79.4 s | 1.15 GiB | 0 | 1.1 s | 0 |
| `qwen2_1_5b` | 42 ms | 28 | 110.2 s | 1.22 GiB | 1 | 752 ms | 0 |
| `gemma3_1b` | 117 ms | 26 | 91.6 s | 1.34 GiB | 0 | 992 ms | 0 |
| `phi3_5` | 37 ms | 32 | 148.1 s | 2.24 GiB | 1 | 1.9 s | 0 |
| `smollm2_1_7b` | 125 ms | 24 | 75.9 s | 1.14 GiB | 0 | 540 ms | 0 |
| `deepseek_qwen_1_5b` | 60 ms | 28 | 79.3 s | 1.04 GiB | 0 | 886 ms | 0 |
| `qwen8b` | 36 ms | 36 | 338.5 s | 4.68 GiB | 0 | 3.5 s | 0 |

Aggregate infra:

- Total benchmark wall time: ~20m37s.
- Total install/sync time: 969.1s.
- Total downloaded bytes: 13.42 GiB.
- Average download throughput: 14.37 MiB/s.
- Average planner latency: 64.24 ms.
- Max planner latency: 125.07 ms.

## Endpoint Validation

The endpoint model is present in runtime graphs emitted by successful sessions. Example for TinyLlama:

- `tokenizer`: `node-b:9002`
- `embedding`: `node-b:9002`
- `output_head`: `node-b:9002`
- pipeline stage #0: `node-a:9001`
- pipeline stage #1: `node-b:9002`
- pipeline stage #2: `node-c:9003`

This confirms the architectural change: services are now represented as advertised endpoints in the runtime graph and passed through orchestrator/node-agent/worker configuration.

## Failure Analysis

The benchmark runner itself exited successfully because it records scenario-stage failures as benchmark data. The runtime failures are real and should be tracked separately from infra success.

Observed failure classes:

1. `remote embedding service failed`
   - Seen in `tinyllama`, `qwen2_1_5b`, `deepseek_qwen_1_5b`.
   - Session creation succeeds, warmup may succeed, but measured generate fails quickly with HTTP 503.
   - Likely next isolation point: service lifecycle or state reuse after warmup/generate, not the endpoint host routing itself.

2. `tokenizer service failed on node-b`
   - Seen in `llama3_1b`, `smollm2_1_7b`.
   - Session creation succeeds, but tokenization fails before pipeline generation.
   - Cleanup sometimes sees `Connection reset by peer` on `node-b`, suggesting worker/service crash during tokenization path.

3. `node-c` unavailable / connection refused
   - Seen in `gemma3_1b` cleanup and in `phi3_5` / `qwen8b` session setup.
   - Larger models make the final pipeline node fragile in this Docker memory envelope.
   - Docker compose shows nodes restored after runner restarts, but scenario traces capture temporary `node-c` refusal.

## Gate Status

| Gate | Result |
|------|--------|
| Full 8-model runner completion | PASS |
| Manifest for all models | PASS |
| Layout for all models | PASS |
| Coverage READY for all models | PASS |
| Layer-first materialization count | PASS (`0` everywhere) |
| Explicit endpoint graph serialization | PASS |
| Session creation for all models | FAIL (6/8) |
| Warmup generate | PARTIAL (3/6 sessions produced warmup tokens) |
| Measured 32-token generate | FAIL (0/8) |
| Valid TPS numbers | NOT MEASURED |

## Conclusion

The new endpoint-aware layer-first architecture is now strong enough to run the full Docker benchmark matrix through model registration, descriptor/manifest creation, layout, install, coverage, and layer-first runtime loading without falling back to GGUF materialization.

The remaining work is runtime service robustness after session creation: tokenizer/embedding service calls and node stability under larger model runtime load. The next debugging pass should focus on preserving service readiness across warmup and measured generate, then reproducing `node-c` crashes on `phi3_5` and `qwen8b` with container memory/RSS evidence.

