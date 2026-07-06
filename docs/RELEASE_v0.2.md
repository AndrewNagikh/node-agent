# v0.2.0 — Layer-first runtime freeze

Tag: `v0.2.0`

Date: 2026-07-06

## Summary

Release `v0.2.0` freezes the first fully working Task 11 layer-first distributed runtime architecture.

The release is based on the successful 3-node Docker Task 11 matrix after the lifecycle stability fix:

- Full benchmark bundle: `logs/benchmark/task11_full_lifecycle_fix_20260706/`
- Lifecycle fix report: `docs/TASK_11_LIFECYCLE_STABILITY_FIX_20260706.md`
- Architecture and metrics report: `docs/TASK_11_FULL_METRICS_AND_ARCHITECTURE_REPORT_20260706.md`

## Release Highlights

- 8/8 Task 11 model scenarios completed warmup and measured generation.
- Largest verified model: `qwen3-8b`.
- Layer-first runtime binding remained active across the matrix.
- Legacy full worker GGUF materialization stayed disabled on the inference path.
- Pipeline worker readiness and lifecycle state handling were hardened.
- Stale `READY` worker state is now reconciled with child process liveness.
- Orchestrator can recover a transient broken pipeline once during `session/generate`.

## Verified Matrix

| Model | Architecture | Warmup | Generate | Tokens | TPS |
|---|---|---:|---:|---:|---:|
| `tinyllama` | llama | 200 | 200 | 32 | 17.1 |
| `llama3_1b` | llama | 200 | 200 | 32 | 15.2 |
| `qwen2_1_5b` | qwen2 | 200 | 200 | 32 | 14.9 |
| `gemma3_1b` | gemma3 | 200 | 200 | 32 | 15.6 |
| `phi3_5` | phi3 | 200 | 200 | 32 | 4.7 |
| `smollm2_1_7b` | llama/smollm | 200 | 200 | 32 | 14.4 |
| `deepseek_qwen_1_5b` | qwen2/deepseek | 200 | 200 | 32 | 14.5 |
| `qwen8b` | qwen3 | 200 | 200 | 32 | 2.25 |

## Architecture Frozen in This Release

The release freezes the following production architecture:

- Runtime descriptor driven role planning.
- Semantic layer/blob install planning.
- Layer Store backed sparse model loading.
- Separate tokenizer, embedding, output head, and sampler runtime roles.
- Three-stage pipeline workers with explicit layer ranges.
- Worker readiness state machine:
  - `STARTING`
  - `MODEL_LOADING`
  - `LISTENER_READY`
  - `PIPE_READY`
  - `READY`
  - `FAILED`
  - `STOPPED`
- Model-agnostic lifecycle recovery for transient pipeline breaks.

## Verification

Release verification completed:

```bash
cmake --build llama.cpp/build --target split_gen3_a split_gen3_b split_gen3_c node_agent orchestrator -j8
cmake --build llama.cpp/build --target test-runtime-presmoke -j8
llama.cpp/build/bin/test-runtime-presmoke
DIST_GRAPH_TRACE=0 DIST_PIPE_TRACE=0 BENCHMARK_DOCKER=1 \
  python3 benchmarks/benchmark_runner.py \
  --profile task11_docker \
  --output-dir logs/benchmark/task11_full_lifecycle_fix_20260706
```

## Known Follow-ups

- `phi3_5` is functionally correct but slower than other small models.
- Add persistent worker close-reason counters to node status.
- Add repeated lifecycle soak tests for session create / warmup / generate / cleanup.

## GitHub Release

```bash
gh release create v0.2.0 \
  --title "v0.2.0 — Layer-first runtime freeze" \
  --notes-file docs/RELEASE_v0.2.md
```
