# Task 11 Lifecycle Stability Fix Report

Date: 2026-07-06

## Summary

The recurring full-matrix `qwen3-8b` runtime failure was fixed as a model-agnostic lifecycle issue.

The fix does not add Qwen-specific behavior. It hardens pipeline worker lifecycle semantics and adds one orchestrator-side recovery attempt for transient pipeline breaks during `session/generate`.

## Root Cause

The previous failure mode was:

- `qwen3-8b` reached install, coverage, materialization, and session creation.
- Warmup failed with `prefill failed`.
- The measured generate then failed with `failed to connect to local pipeline ctrl port`.
- Middle/final worker logs showed `recv cmd failed`.
- Node status could still report stale `READY` states because readiness was read from the last worker state file and was not reconciled with child process liveness.

This meant the orchestrator could keep treating a broken pipeline as session-ready after a worker exited or after the persistent A->B/B->C pipe was broken.

## Changes

Implemented:

- Child process liveness polling via `dist_process_is_running()`.
- Node `/status` now reconciles worker ready state with the actual worker pid; dead workers are reported as `FAILED` instead of stale `READY`.
- Pipeline workers now write terminal states:
  - `STOPPED` for normal shutdown.
  - `FAILED` for abnormal accept/pipe/decode loop exits.
- Entry worker now marks the pipeline as `FAILED` and exits the accept loop on downstream pipe failure instead of continuing with a broken persistent peer socket.
- Orchestrator `/session/generate` now attempts one model-agnostic pipeline recovery:
  - stop existing session workers,
  - reconfigure the same runtime graph/stage layout,
  - wait for worker readiness,
  - retry generation once,
  - record recovery metadata in timing if used.

## Verification

Build:

- `cmake --build llama.cpp/build --target split_gen3_a split_gen3_b split_gen3_c node_agent orchestrator -j8`
- Result: passed.

Pre-smoke:

- `cmake --build llama.cpp/build --target test-runtime-presmoke -j8`
- `llama.cpp/build/bin/test-runtime-presmoke`
- Result: passed.

Targeted Qwen8B:

- Output: `logs/benchmark/task11_qwen8b_lifecycle_fix_20260706/`
- Scenario: `qwen8b_c3_p16_g32`
- Warmup: HTTP `200`
- Generate: HTTP `200`
- Measured tokens: `32`
- TPS: `6.48 tok/s`
- Prefill: `2482 ms`
- Decode/token: `85.8 ms`
- Recovery metadata: not present, meaning the primary path passed without retry.

Full Task 11 matrix:

- Output: `logs/benchmark/task11_full_lifecycle_fix_20260706/`
- Scenarios: `8/8`
- Runner exit code: `0`
- Warmup/generate: `8/8` models passed.
- `qwen3-8b` warmup: HTTP `200`
- `qwen3-8b` generate: HTTP `200`
- `qwen3-8b` measured tokens: `32`
- `qwen3-8b` TPS: `2.25 tok/s`
- `qwen3-8b` prefill: `3471 ms`
- `qwen3-8b` decode/token: `335 ms`

Full matrix TPS summary:

- Average generate TPS: `12.33`
- Max generate TPS: `17.07`
- `phi3_5` now remains functional but slow: `4.7 tok/s`

## Current Status

The previous lifecycle/runtime stability blocker is resolved for the Task 11 Docker matrix.

Remaining follow-up:

- Add persistent close-reason counters to node status, not only terminal worker states.
- Investigate `phi3_5` performance separately.
- Add a repeated lifecycle soak test for session create / warmup / generate / cleanup.
