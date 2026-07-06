# v0.2.1 — Distributed test surface cleanup

Tag: `v0.2.1`

Date: 2026-07-06

## Summary

Release `v0.2.1` keeps the `v0.2.0` frozen layer-first runtime architecture unchanged and cleans up the distributed test source layout.

The active release test surface now matches the `v0.2.0` verification gates:

- 20 active `test-*.cpp` sources remain at `llama.cpp/tools/distributed/`.
- 137 legacy or experimental `test-*.cpp` sources are archived under `llama.cpp/tools/distributed/legacy_tests/`.
- Active CMake test targets remain limited to release-critical runtime gates and the intentionally unconditional E2E targets.

## Active Test Surface

The active distributed test files are:

- `test-cluster-e2e*.cpp`
- `test-runtime-presmoke.cpp`
- `test-runtime-acceptance.cpp`
- `test-runtime-descriptor.cpp`
- `test-runtime-schema.cpp`
- `test-runtime-layer-store-model-load.cpp`
- `test-layer-store-tensor-provider.cpp`
- `test-runtime-worker-bind.cpp`
- `test-runtime-install-planning.cpp`
- `test-runtime-role-planner.cpp`
- `test-runtime-cost.cpp`
- `test-runtime-config.cpp`
- `test-embedding-service.cpp`
- `test-runtime-service.cpp`

Production verification tools such as `verify_hidden_pipeline`, `verify_logits_pipeline`, `verify_final_runtime`, and related `verify_*` binaries remain in place.

## Verification

Release verification completed with the same release-critical build and unit gate set used for `v0.2.0`:

```bash
cmake --build llama.cpp/build --target \
  orchestrator node_agent split_gen3_a split_gen3_b split_gen3_c \
  test-runtime-presmoke test-runtime-acceptance test-runtime-descriptor \
  test-runtime-schema test-runtime-layer-store-model-load \
  test-layer-store-tensor-provider test-runtime-worker-bind \
  test-runtime-install-planning test-runtime-role-planner \
  test-runtime-cost test-runtime-config test-embedding-service \
  test-runtime-service mono_reference verify_decode_loop_parity \
  verify_hidden_pipeline verify_logits_pipeline verify_layer_equivalence \
  verify_hidden_transport verify_runtime_api verify_final_runtime \
  verify_final_logits verify_decode_graph verify_context_params -j8
```

## Relationship to v0.2.0

This is a cleanup-only release. Runtime architecture, benchmark result, and lifecycle stability status remain those documented in:

- `docs/RELEASE_v0.2.md`
- `docs/TASK_11_LIFECYCLE_STABILITY_FIX_20260706.md`
- `docs/TASK_11_FULL_METRICS_AND_ARCHITECTURE_REPORT_20260706.md`
