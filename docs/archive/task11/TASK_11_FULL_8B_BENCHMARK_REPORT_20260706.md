# Task 11 Full Docker Benchmark Report, Models up to 8B

Date: 2026-07-06  
Run ID: `20260706_104139`  
Profile: `task11_docker`  
Result bundle: `logs/benchmark/task11_full_8b_20260706`

## Executive Summary

The new layer-first runtime architecture was benchmarked on a 3-node Docker cluster across all Task 11 model families up to `qwen3-8b`.

The infrastructure path is now working consistently across the full set:

- All 8 models registered, discovered, produced manifests, planned layouts, synchronized required blobs, reached coverage `READY`, skipped legacy full materialization, and created runtime sessions.
- 6 of 8 models completed measured generation with 32 output tokens.
- The previously fixed remote lifecycle issue did not recur: successful models passed warmup and measured generate after remote/local reset handling.
- The two remaining generation failures, `phi-3.5-mini` and `qwen3-8b`, have a different failure mode: entry worker startup/connect readiness race, not endpoint routing or KV cache state.

The architecture is therefore past the previous universal `generate` failure. The remaining work is focused: pipeline worker startup readiness for heavier models where stage B is not accepting `node-b:9113` before stage A attempts to connect.

## Cluster and Run Configuration

- Orchestrator: `http://127.0.0.1:9000`
- Cluster: 3 Docker nodes
- Node memory at run start:
  - `node-a`: 6.97 / 7.65 GB RAM free
  - `node-b`: 6.97 / 7.65 GB RAM free
  - `node-c`: 6.96 / 7.65 GB RAM free
- GPU/VRAM: none
- Prompt length: 16 tokens
- Warmup: 4 tokens
- Measured generate: 32 tokens
- Runtime mode: layer-first / descriptor-driven Task 11
- Docker behavior: node restart between scenarios, worker release before/between models, purge model after scenario

## Result Matrix

| Model | Architecture | Layers | Fit | Coverage | Session | Warmup | Generate | Tokens | TPS | Install |
|---|---:|---:|---|---|---|---|---|---:|---:|---:|
| `tinyllama-1.1b` | llama | 22 | yes | READY | 200 | 200 | 200 | 32 | 14.64 | 42.9s |
| `llama-3.2-1b` | llama | 16 | yes | READY | 200 | 200 | 200 | 32 | 13.13 | 82.1s |
| `qwen2.5-1.5b` | qwen2 | 28 | yes | READY | 200 | 200 | 200 | 32 | 13.92 | 73.3s |
| `gemma-3-1b` | gemma | 26 | yes | READY | 200 | 200 | 200 | 32 | 13.97 | 94.0s |
| `phi-3.5-mini` | phi3 | 32 | yes | READY | 200 | 503 | 503 | 0 | 0.00 | 167.1s |
| `smollm2-1.7b` | smolllm | 24 | yes | READY | 200 | 200 | 200 | 32 | 15.24 | 73.0s |
| `deepseek-r1-distill-qwen-1.5b` | qwen2 | 28 | yes | READY | 200 | 200 | 200 | 32 | 14.40 | 73.7s |
| `qwen3-8b` | qwen3 | 36 | yes | READY | 200 | 503 | 503 | 0 | 0.00 | 302.8s |

## Successful Generation Results

Successful measured generation is now stable for six architecture/model families:

| Model | Generate latency | TPS | Prefill | Decode per token |
|---|---:|---:|---:|---:|
| `tinyllama-1.1b` | 2186.47 ms | 14.64 | 488.65 ms | 63.17 ms |
| `llama-3.2-1b` | 2437.38 ms | 13.13 | 685.51 ms | 68.61 ms |
| `qwen2.5-1.5b` | 2298.75 ms | 13.92 | 512.07 ms | 68.95 ms |
| `gemma-3-1b` | 2290.27 ms | 13.97 | 627.61 ms | 71.09 ms |
| `smollm2-1.7b` | 2099.57 ms | 15.24 | 746.26 ms | 74.30 ms |
| `deepseek-r1-distill-qwen-1.5b` | 2222.08 ms | 14.40 | 523.35 ms | 68.67 ms |

Average TPS across successful measured runs: ~14.22 tok/s.  
Best measured TPS: `smollm2-1.7b`, 15.24 tok/s.  
Slowest successful measured TPS: `llama-3.2-1b`, 13.13 tok/s.

## Install, Coverage, and Materialization

The layer-first distribution path is healthy:

| Model | Install ops | Install bytes | Retries | Download throughput | Coverage latency | Materialization |
|---|---:|---:|---:|---:|---:|---:|
| `tinyllama-1.1b` | 201 | 667.1 MB | 0 | 15.08 MB/s | 629.86 ms | 9.62 ms |
| `llama-3.2-1b` | 151 | 1.23 GB | 0 | 14.44 MB/s | 745.37 ms | 7.25 ms |
| `qwen2.5-1.5b` | 339 | 1.11 GB | 0 | 14.67 MB/s | 878.16 ms | 10.46 ms |
| `gemma-3-1b` | 345 | 1.45 GB | 1 | 15.33 MB/s | 1158.70 ms | 14.75 ms |
| `phi-3.5-mini` | 201 | 2.39 GB | 0 | 13.79 MB/s | 1583.80 ms | 6.73 ms |
| `smollm2-1.7b` | 220 | 1.22 GB | 0 | 16.10 MB/s | 693.40 ms | 14.54 ms |
| `deepseek-r1-distill-qwen-1.5b` | 340 | 1.11 GB | 1 | 14.67 MB/s | 730.35 ms | 11.99 ms |
| `qwen3-8b` | 400 | 5.02 GB | 1 | 16.22 MB/s | 3027.37 ms | 13.89 ms |

Key observations:

- Coverage reached `READY` for every model, including `qwen3-8b`.
- Legacy full worker GGUF materialization did not happen: `worker_gguf_bytes=0` for all scenarios.
- Materialization stage is effectively metadata/runtime binding only, consistently under 15 ms.
- Install throughput is bounded by Docker/HF/range-fetch behavior and stayed in a narrow 13.79-16.22 MB/s band.
- `qwen3-8b` install took ~5 minutes and required one retry, but recovered and reached full coverage.

## Runtime Graph and New Architecture

The benchmark exercised the new architecture end-to-end:

1. Model-specific descriptor path:
   - Manifest and architecture metadata were produced for Llama, Qwen2, Gemma, Phi3, SmolLM2, DeepSeek-Qwen, and Qwen3.
   - Pipeline boundaries were derived from model/runtime metadata, not hardcoded model-specific layer numbers.

2. Layer-first distribution:
   - Nodes received only required semantic blobs/layers.
   - Coverage tracked layer readiness and reconciled missing blobs.
   - Runtime bind loaded from layer store without building a monolithic worker GGUF.

3. Explicit endpoints:
   - Runtime graph roles carry explicit `host`, `port`, and endpoint data.
   - Cross-node calls no longer rely on `127.0.0.1` assumptions.

4. Boundary service placement:
   - `tokenizer` and `embedding` are assigned to entry boundary node.
   - `output_head` and `sampler` are assigned to final boundary node.
   - When a service is colocated with the corresponding boundary worker, the orchestrator avoids launching a duplicate service context.

5. Service reset lifecycle:
   - Remote/local reset paths prevent stale KV state between warmup and measured generate.
   - This is validated by the 6 successful models that complete both warmup and measured generate.

## Remaining Failures

### `phi-3.5-mini`

Infrastructure stages passed:

- fit: true
- coverage: READY, 32/32 layers
- session create: 200
- runtime graph: entry `node-a` [0,11), middle `node-b` [11,22), final `node-c` [22,32)

Warmup and measured generate both failed:

- HTTP status: 503
- note: `failed to connect to local pipeline ctrl port`
- node logs: `gen3_a: connect to B failed node-b:9113`

Interpretation: entry worker loaded enough to start, but stage B was not accepting the peer connection when stage A attempted to connect.

### `qwen3-8b`

Infrastructure stages passed:

- fit: true
- coverage: READY, 36/36 layers
- session create: 200
- runtime graph: entry `node-a` [0,12), middle `node-b` [12,24), final `node-c` [24,36)

Warmup and measured generate both failed:

- HTTP status: 503
- note: `failed to connect to local pipeline ctrl port`
- node logs: `gen3_a: connect to B failed node-b:9113`

Interpretation: same readiness/startup race, amplified by heavier worker load times.

This is a different class from the earlier `remote embedding service failed` / KV cache inconsistency. The KV reset fix remains validated by the successful models.

## Architecture Assessment

The new architecture is now working for model families that previously failed universally at `generate`. It is no longer blocked at endpoint routing, remote service lifecycle, descriptor/runtime graph construction, install planning, coverage, or zero-materialization runtime binding.

The current boundary is worker orchestration readiness:

- Stage A attempts to connect to stage B during startup.
- For heavier models, stage B can still be loading or not yet listening.
- The orchestrator currently treats process spawn/session create as sufficient readiness.
- The pipeline needs a stage-level readiness handshake before generation is allowed.

## Recommended Next Fix

Add explicit pipeline worker readiness:

1. Each worker reports readiness after:
   - model context is loaded,
   - peer listener is bound for middle/final stages,
   - entry control port is bound,
   - outbound peer target has been confirmed or deferred until generate.

2. `node_agent /runtime/configure` should not return ready until the worker is actually reachable.

3. Orchestrator session creation should wait for all pipeline stages:
   - final peer listener ready,
   - middle peer listener ready,
   - entry ctrl listener ready,
   - only then allow warmup/generate.

4. Entry worker should retry `connect to B` with bounded backoff instead of failing once.

Expected impact: `phi-3.5-mini` and `qwen3-8b` should move from 503 startup failures to actual generation or a later compute/memory-specific failure.

## Conclusion

The full up-to-8B Docker benchmark shows substantial progress:

- 8/8 models pass layer-first install, coverage, zero-materialization binding, and session creation.
- 6/8 models complete measured distributed generation.
- Remaining failures are isolated to pipeline startup readiness for heavier workers.

This is a much narrower and more actionable state than the previous benchmark where `generate` failed across all models.
