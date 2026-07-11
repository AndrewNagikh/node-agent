# Task 17.3 — Entry GPU Sync Relocation / Overlap

**Type:** Implementation (design already researched in Task 15.2)
**Status:** Planned
**Parent:** Research 17 (roadmap R3)
**Depends on:** **Task 17.1** (async dispatch creates the window the sync can hide in)
**Expected gain:** +~14% (4.72 ms `LLAMA_BACKEND_SYNCHRONIZE` off the serial path)

---

## Problem

Task 15.1b/15.2 proved the ~5 ms "gather" is **not transport**: 83% is `ggml_backend_sched_synchronize()` — GPU completion wait for entry partial-forward + async D2H, placed inside `llama_get_embeddings()` by API contract. The *wait* is physics; its *position on the serial path* is not (Task 15.2 §9). With serial dispatch there is nowhere to hide it; after 17.1 the dispatch gap becomes schedulable overlap space.

## Scope

Implement one of the pre-researched options from Task 15.2 §8, in preference order:

1. **Option A — relocate:** explicit `llama_synchronize()` at end of entry decode window; gather reads via already-synced context (single sync per token; avoid the per-token double fence noted in 15.2 Q9 for `n_tokens > 1`).
2. **Option C — overlap:** run the sync concurrently with work that does not depend on the hidden buffer (next-wave dispatch, ack handling), with explicit buffer-lifetime rules: `embd.data` must not be consumed by send before sync completes, and the next `llama_decode` must not start before the send has copied out.
3. **Option B — one sync + `get_embeddings_ith` loop** for multi-token (prefill) waves.

Uses existing llama.cpp public APIs only (`llama_synchronize` + result accessors); **no GGML/llama.cpp core changes**.

## Acceptance criteria

| Gate | Threshold | Source |
|------|-----------|--------|
| `LLAMA_BACKEND_SYNCHRONIZE` inside gather span | ≤ 1 ms avg (moved or overlapped) | Task 15.1b breakdown re-run |
| Gather (`HIDDEN_TRANSFER` link=ab) span | ≤ 1.5 ms avg | trace analysis |
| Serial critical path | reduced ≥ 3.5 ms vs post-17.1 baseline | `critical_path.json` |
| Correctness | hidden parity: middle receives identical hidden vs baseline (checksum probe run) | parity harness |
| No new stalls | bubble does not regress above 17.1 gate | `bubble.json` |

## Risks (from 15.2)

Double-sync (decode-path + get-path) negating the gain; hidden buffer overwritten by next decode when overlap ordering is wrong; perf-accounting side effects inside `synchronize()`.

## Non-goals

GPU-resident hidden / D2D transport (Task 15.3 Models C/D — blocked by cross-vendor process isolation); wire format changes.

## References

`docs/archive/TASK_15_2_GPU_SYNCHRONIZATION_STUDY.md` §8–9; `docs/archive/TASK_15_1b_HIDDEN_GATHER_ROOT_CAUSE.md`; `docs/archive/TASK_15_3_HIDDEN_OWNERSHIP_STUDY.md`; Study §6.2.
