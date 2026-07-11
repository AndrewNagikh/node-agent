# Task 18.2 — Layer Sync at Wire Speed

**Type:** Investigation → Implementation (install/sync path only; runtime untouched)
**Status:** Planned
**Parent:** Research 17 (Study §7.2); required by 70B target (`docs/TARGET_70B_GOAL_AND_FEASIBILITY.md`)
**Expected gain:** cold model distribution **hours → minutes** (~50× measured headroom)

---

## Problem

Measured sync throughput on homelab is **1.3–14 MiB/s** (LAN benchmark 20260706): TinyLlama 0.67 GB took 286 s where 1 GbE allows ~6 s. Extrapolated to the 70B target (~34 GB Q3), the current path costs **1–7 hours**; the wire floor is **~5–7 minutes** (34 GB / ~110 MiB/s effective, source uplink shared by 3 pulling nodes). The goal explicitly excludes download time from the "local-like" budget, but hours-long sync makes the 70B ladder untestable in practice.

## Phase A — Attribute (no changes)

Instrument one cold model install per node with per-stage timing:

1. per-blob HTTP request lifecycle: connection reuse? request latency vs transfer time (345+ install ops for a 1.5B model suggests per-op overhead dominates)
2. concurrency: how many blobs in flight per node; are nodes pulling serially from the orchestrator/source?
3. disk write path: fsync/verify per blob?
4. source-side bottleneck: source uplink saturation vs per-request stalls
5. retry/reconcile overhead (coverage refresh cost)

**Exit:** a table where blob-size buckets × stage explain ≥ 90% of wall time; verdict: *per-request latency* vs *concurrency* vs *disk* vs *source uplink*.

## Phase B — Fix (scope from Phase A; candidates)

- persistent HTTP connections + pipelined/parallel blob fetch per node (bounded concurrency)
- blob coalescing: range requests over contiguous tensor regions instead of hundreds of small ops
- node-to-node (P2P) blob sharing so the source uplink is not the shared bottleneck for 3 pullers (nodes that already hold a blob serve peers) — evaluate only if Phase A shows uplink saturation
- deferred verify (checksum overlapped with download)

## Acceptance criteria

| Gate | Threshold | Source |
|------|-----------|--------|
| Phase A attribution | ≥ 90% of sync wall time | new sync trace artifact |
| Cold sync, qwen8b (4.7 GB, 3 nodes) | ≤ 2.5 min (≥ 70% of shared-uplink wire floor) | benchmark install metrics |
| Cold sync, 70B Q3 (~34 GB) | ≤ 10 min | ladder rung L3 run |
| Warm (cached) sync | unchanged ~0 | coverage READY timing |
| Integrity | blob checksums verified; coverage READY 100%; reconcile clean | coverage report |
| No decode/session regression | infra benchmark passes unchanged | benchmark matrix |

## Non-goals

Compression/delta formats (quant files are near-incompressible); WAN distribution; changing the layer-store blob schema.

## References

`docs/archive/LAN_HOMELAB_BENCHMARK_REPORT_20260706.md` (throughput table, install ops counts); Study §7.2 (wire-floor calculation); `docs/TARGET_70B_GOAL_AND_FEASIBILITY.md` §3.
