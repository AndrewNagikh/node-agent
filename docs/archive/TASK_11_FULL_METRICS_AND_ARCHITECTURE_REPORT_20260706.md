# Task 11 Full Metrics and Architecture Report

Date: 2026-07-06

Run ID: `20260706_161248`

Profile: `task11_docker`

Artifacts: `logs/benchmark/task11_full_metrics_20260706/`

Generated files:
- `logs/benchmark/task11_full_metrics_20260706/results.json`
- `logs/benchmark/task11_full_metrics_20260706/results.csv`
- `logs/benchmark/task11_full_metrics_20260706/report.md`
- `logs/benchmark/task11_full_metrics_20260706/report.html`

## Lifecycle Stability Update

The Qwen8B lifecycle/runtime issue described in this report has been fixed and re-verified.

Superseding validation artifacts:
- Targeted Qwen8B run: `logs/benchmark/task11_qwen8b_lifecycle_fix_20260706/`
- Full Task 11 matrix after fix: `logs/benchmark/task11_full_lifecycle_fix_20260706/`
- Fix report: `docs/TASK_11_LIFECYCLE_STABILITY_FIX_20260706.md`

Post-fix status:
- Full matrix runner exit code: `0`
- Scenarios: `8/8`
- Warmup/generate: `8/8` models passed
- `qwen3-8b`: warmup HTTP `200`, generate HTTP `200`, 32 measured tokens, `2.25 tok/s`

## Executive Summary

The full Task 11 Docker benchmark completed with runner exit code `0`.

The new layer-first distributed runtime is now passing the infrastructure and session setup path across all 8 model families in the benchmark matrix:

- 8/8 models fit the 3-node cluster according to the runtime planner.
- 8/8 models reached layer coverage `READY`.
- 8/8 models created runtime sessions successfully.
- 8/8 models used layer-first runtime binding with `worker_gguf_bytes=0`; no legacy full worker GGUF materialization occurred.
- 7/8 models completed warmup and measured generation with 32 measured output tokens.
- `qwen3-8b` passed install, coverage, materialization, and session creation, but failed warmup/measured generation in the full matrix with `prefill failed` / `failed to connect to local pipeline ctrl port`.

The measured successful models produced 224 measured tokens. Average measured TPS across successful generate runs was `12.94 tok/s`; the aggregate benchmark report average including the failed Qwen8B run was `11.32 tok/s`.

## Benchmark Configuration

Cluster:
- Docker `orchestrator + node-a + node-b + node-c`
- 3 nodes
- Initial free RAM: `20.77 GB` total across nodes
- VRAM: `0 GB`; CPU backend
- Node score: approximately `137.94`

Benchmark profile:
- Models: `tinyllama`, `llama3_1b`, `qwen2_1_5b`, `gemma3_1b`, `phi3_5`, `smollm2_1_7b`, `deepseek_qwen_1_5b`, `qwen8b`
- Prompt length: `16`
- Warmup tokens: `4`
- Measured generate tokens: `32`
- Runtime mode: `task11`
- Docker nodes restarted between scenarios
- Worker release and model purge enabled between scenarios
- Diagnostic graph/pipe tracing disabled for the measured full run

## Result Matrix

| Model | Model ID | Layers / placements | Install | Coverage | Session | Warmup | Generate | Tokens | TPS | Prefill | Decode/token |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `tinyllama` | `tinyllama-1.1b` | 22 | 60.6s | 362ms | 1.81s | 200 | 200 | 32 | 16.24 | 624ms | 67ms |
| `llama3_1b` | `llama-3.2-1b` | 16 | 94.1s | 740ms | 2.97s | 200 | 200 | 32 | 14.21 | 490ms | 65ms |
| `qwen2_1_5b` | `qwen2.5-1.5b` | 28 | 110.2s | 685ms | 2.34s | 200 | 200 | 32 | 14.95 | 546ms | 67ms |
| `gemma3_1b` | `gemma-3-1b` | 26 | 101.5s | 976ms | 2.73s | 200 | 200 | 32 | 13.73 | 649ms | 63ms |
| `phi3_5` | `phi-3.5-mini` | 32 | 165.7s | 1.44s | 4.79s | 200 | 200 | 32 | 2.21 | 3.45s | 231ms |
| `smollm2_1_7b` | `smollm2-1.7b` | 24 | 81.9s | 614ms | 1.98s | 200 | 200 | 32 | 14.62 | 602ms | 68ms |
| `deepseek_qwen_1_5b` | `deepseek-r1-distill-qwen-1.5b` | 28 | 100.1s | 654ms | 2.31s | 200 | 200 | 32 | 14.62 | 679ms | 68ms |
| `qwen8b` | `qwen3-8b` | 36 | 403.3s | 4.98s | 9.38s | 503 | 503 | 0 | 0.00 | n/a | n/a |

## Performance Observations

Fastest measured generation:
- `tinyllama`: `16.24 tok/s`
- `qwen2_1_5b`: `14.95 tok/s`
- `smollm2_1_7b` and `deepseek_qwen_1_5b`: `14.62 tok/s`

Slowest successful generation:
- `phi3_5`: `2.21 tok/s`

The `phi3_5` result is now functionally successful but significantly slower than the other successful models. It has the highest successful prefill time (`3.45s`) and decode-per-token time (`231ms`). That should be treated as a performance follow-up, not as a correctness blocker.

Qwen8B:
- Installed `5.02 GB` of layer-first data.
- Reached coverage `READY 36/36`.
- Created a session in `9.38s`.
- Failed warmup with `prefill failed`.
- Failed measured generate with `failed to connect to local pipeline ctrl port`.

This failure is runtime lifecycle instability in the full-matrix run, not a planner/coverage/materialization failure. A separate Qwen8B-only diagnostic run with the same architecture succeeded with 32 measured tokens and approximately `7.58 tok/s`, so the current evidence does not support a Qwen-specific model patch.

## Install and Coverage Metrics

| Model | Downloaded bytes | Install ops | Retries | Download throughput | Coverage ready |
|---|---:|---:|---:|---:|---:|
| `tinyllama` | 0.67 GB | 201 | 0 | 10.57 MB/s | 362ms |
| `llama3_1b` | 1.23 GB | 151 | 0 | 12.58 MB/s | 740ms |
| `qwen2_1_5b` | 1.14 GB | 345 | 1 | 10.00 MB/s | 685ms |
| `gemma3_1b` | 1.44 GB | 343 | 1 | 13.83 MB/s | 976ms |
| `phi3_5` | 2.40 GB | 202 | 1 | 14.07 MB/s | 1.44s |
| `smollm2_1_7b` | 1.22 GB | 220 | 0 | 14.32 MB/s | 614ms |
| `deepseek_qwen_1_5b` | 1.11 GB | 339 | 0 | 10.66 MB/s | 654ms |
| `qwen8b` | 5.02 GB | 400 | 1 | 12.13 MB/s | 4.98s |

Total install time across the full matrix was approximately `1117.5s`.

Coverage behavior is healthy: every model reached `READY`, including Qwen8B after `36/36` layer coverage. This validates the runtime descriptor, layer store, reconciliation, and coverage refresh path across all target model families up to 8B.

## Session and Runtime Memory

| Model | Session create | Weights | Required memory |
|---|---:|---:|---:|
| `tinyllama` | 1.81s | 0.62 GiB | 1.02 GiB |
| `llama3_1b` | 2.97s | 0.74 GiB | 1.14 GiB |
| `qwen2_1_5b` | 2.34s | 1.04 GiB | 1.43 GiB |
| `gemma3_1b` | 2.73s | 0.74 GiB | 1.13 GiB |
| `phi3_5` | 4.79s | 2.23 GiB | 2.65 GiB |
| `smollm2_1_7b` | 1.98s | 0.98 GiB | 1.38 GiB |
| `deepseek_qwen_1_5b` | 2.31s | 1.04 GiB | 1.43 GiB |
| `qwen8b` | 9.38s | 4.68 GiB | 5.19 GiB |

Materialization remains metadata/runtime binding rather than full model reconstruction:
- `worker_gguf_bytes=0` for all models.
- Materialization latency was `6-16ms`.
- Runtime model loading happens through layer-store tensor providers and sparse tensor selection.

## New Architecture Summary

The benchmark validates the new layer-first architecture:

1. Manifest-first model registration
   - The orchestrator reads GGUF metadata and tensor manifests without requiring full local materialization on every worker.
   - Runtime descriptors define semantic blobs such as embedding, transformer layers, output norm, and output head.

2. Layer-first placement and install
   - The planner maps semantic layer blobs to nodes.
   - Pipeline stages receive layer ranges such as `[0,12)`, `[12,24)`, `[24,36)`.
   - Install operations download only required tensor ranges into each node layer store.

3. Coverage and reconciliation
   - Coverage tracks layer readiness across nodes.
   - Reconciliation repairs missing blobs and advances the model to `READY`.
   - Qwen8B coverage reached `READY` after downloading `5.02 GB`, proving the large-model distribution path works.

4. Runtime descriptor execution
   - Session creation builds a runtime graph with service roles:
     - tokenizer
     - embedding
     - output head / sampler
     - pipeline stages
   - Workers are configured from runtime descriptor stage ranges rather than hardcoded model assumptions.

5. Sparse runtime model loading
   - `llama_model_init_from_user_filtered` allows runtime workers to load only required tensors.
   - Worker memory is reduced by avoiding full GGUF materialization.
   - Entry stages include embedding tensors when `layer_start=0`.
   - Final stages include output norm/head tensors when `layer_end == n_layer`.
   - Layer tensors are kept by generic `blk.<layer>.*` range matching.

6. Readiness state machine
   - Workers transition through explicit states rather than a coarse `RUNNING` status:
     - `STARTING`
     - `MODEL_LOADING`
     - `LISTENER_READY`
     - `PIPE_READY`
     - `READY`
     - `FAILED`
   - Orchestrator session readiness waits on worker readiness and pipeline listener readiness.

7. Graph construction instrumentation
   - `llm_graph_trace_scope` and graph trace hooks were added for diagnostics.
   - These traces proved the earlier Phi/TinyLlama stalls were caused by missing tensors in sparse runtime loading, not by model-specific graph logic.
   - Graph tracing is controlled by `DIST_GRAPH_TRACE` and is disabled for measured benchmark runs.

8. Pipeline runtime instrumentation
   - `DIST_PIPE_TRACE` was added for model-agnostic diagnosis of runtime prefill/decode flow across entry, middle, and final workers.
   - It is disabled for measured benchmark runs.

## Changes Made in This Workstream

Core runtime API:
- Added tensor filtering API for user-backed model loading.
- Extended context params with distributed layer range controls:
  - `layer_start`
  - `layer_end`
  - `skip_output_head`

Model loading:
- Added filtered tensor creation in `llama_model_loader`.
- Added layer-store backed sparse runtime loading.
- Included generic fallback for all tensors matching assigned transformer layer ranges.
- Included embedding tensors for first pipeline stages.
- Included output norm/head tensors for final pipeline stages.

Workers:
- Passed stage layer ranges into contexts before graph reservation.
- Set distributed worker `n_ubatch=1` to reduce graph/memory pressure.
- Added pipeline worker readiness states and listener readiness.
- Improved peer connect/retry behavior.
- Added optional `DIST_PIPE_TRACE` instrumentation.

Graph diagnostics:
- Added `llm_graph_trace_scope`.
- Instrumented graph reserve and common graph input builders.
- Instrumented Phi3 and Llama graph constructors.
- Used the traces to remove a wrong Phi entry input-path assumption and restore the generic entry path.

Benchmark tooling:
- Made session creation timeout configurable for layer-first runtime.
- Added stage-level runtime snapshots to benchmark results.
- Preserved result bundles with JSON, CSV, markdown, and HTML reports.

Docker:
- Added `DIST_GRAPH_TRACE` and `DIST_PIPE_TRACE` environment controls.

## Current Status

Passed:
- Full infrastructure path across 8/8 models.
- Layer-first coverage across 8/8 models.
- Runtime session creation across 8/8 models.
- Warmup/generate across 7/8 models.
- Phi3 now completes measured generation, although performance is slower than other successful models.

Remaining risk:
- Qwen8B generation is not stable in the full matrix lifecycle.
- The failure is after session creation, during prefill/control connection lifecycle.
- Evidence from full run:
  - Warmup: HTTP `503`, `prefill failed`
  - Measured generate: HTTP `503`, `failed to connect to local pipeline ctrl port`
  - Entry worker stayed reported as `READY`; middle/final reported ready worker PIDs but downstream logs show `recv cmd failed`.
- Evidence from separate Qwen-only diagnostic run:
  - Warmup and measured generation passed.
  - 32 measured tokens at approximately `7.58 tok/s`.

Interpretation:
- Do not add Qwen-specific logic.
- Treat the remaining issue as model-agnostic runtime lifecycle stability under full-matrix churn: repeated Docker restarts, model purge/reinstall, worker shutdown/restart, and large-model prefill timing.

## Recommended Next Steps

1. Add first-class pipe-stage timing counters to production diagnostics, not only stderr tracing:
   - entry local decode time
   - entry send hidden time
   - middle decode time
   - middle forward-to-final time
   - final decode/sample time
   - ctrl connection lifetime and close reason

2. Make worker close reasons observable in node status:
   - normal client disconnect
   - downstream recv failed
   - upstream send failed
   - decode failed
   - shutdown requested

3. Run Qwen8B repeated lifecycle soak:
   - clean cluster
   - repeated session create / warmup / generate / cleanup
   - no model reinstall between iterations
   - then repeat with purge/reinstall to isolate full-matrix churn.

4. Add a benchmark retry mode for runtime generation only:
   - retry once after worker reset
   - record both first failure and retry result
   - do not hide failures from the report.

5. Profile Phi3 performance separately:
   - Phi3 is now correct, but prefill/decode latency is materially worse than peer models.
   - This should be investigated as a performance track after runtime stability.

