# Task 13.6 — v1 Deprecation (RFC-0013 Phase 6)

**Status:** Done (Docker 1B; homelab validation deferred)  
**RFC:** [`RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md`](RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md)  
**Depends on:** Task 13.5 (full async client pipelining)  
**Phase:** Migration §25 Phase 6 — v1 deprecation

---

## Goal

Make **protocol v2 the default** on all nodes. Retain **v1 RPC rollback** behind an explicit flag for the migration window (RFC: 30-day retention).

v1 blocking code paths remain in tree for rollback; they are not removed in this phase.

---

## Defaults (Phase 6)

| Env | Default | Rollback |
|-----|---------|----------|
| `DIST_RUNTIME_PROTOCOL_V2` | **on** (unset = v2) | `=0` or `DIST_RUNTIME_PROTOCOL_V1=1` |
| `DIST_RUNTIME_ENTRY_QUEUE` | **on** with v2 | `=0` |
| `DIST_RUNTIME_STAGE_QUEUE` | **on** with v2 | `=0` |
| `DIST_RUNTIME_CLIENT_PIPELINE` | **on** with entry+stage | `=0` |

`docker-compose.yml` service env uses `:-1` defaults for all of the above.

---

## Deprecation

When v1 is negotiated, workers and `node_agent` log once per process:

```
runtime_protocol: DEPRECATED v1 RPC path active (...); v1 rollback support ends after migration window
```

---

## Changes

| Area | Change |
|------|--------|
| `runtime_protocol.cpp` | v2 default; `runtime_protocol_v1_forced()`; deprecation log |
| `wave_inbound_queue.cpp` | Entry/stage queues default on with v2 |
| `docker-compose.yml` | Runtime flags default to `1` |
| `test-runtime-protocol.cpp` | v2 default + v1 rollback tests |
| `scripts/verify_docker_protocol_v2.sh` | Single v2-default run + v1 rollback smoke |

---

## Verification (local Docker 1B)

```bash
./scripts/verify_docker_protocol_v2.sh
```

Includes:
1. v2-default build + benchmark + trace gates  
2. v1 rollback (`DIST_RUNTIME_PROTOCOL_V2=0`) generate smoke + deprecation log

Homelab / remote cluster validation is planned after all phases complete locally.

---

## Rollback procedure

```bash
cd llama.cpp/tools/distributed/docker
DIST_RUNTIME_PROTOCOL_V2=0 \
DIST_RUNTIME_ENTRY_QUEUE=0 \
DIST_RUNTIME_STAGE_QUEUE=0 \
DIST_RUNTIME_CLIENT_PIPELINE=0 \
docker compose up -d --force-recreate
```

---

## RFC-0013 migration complete

| Phase | Status |
|-------|--------|
| 1 Tracing | Done |
| 2 Wire envelope | Done |
| 3 Entry queue | Done |
| 4 Pipeline overlap | Done |
| 5 Full async | Done (Docker CPU bubble ~72%) |
| 6 v1 deprecation | Done |

Performance hard gates (bubble <10%, TPS ≥ 0.8× ceiling) remain **homelab targets** per user plan.
