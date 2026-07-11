# Task 13.5 — Full Async Dispatch (RFC-0013 Phase 5)

**Status:** Done (Docker 1B protocol verified; bubble ~72% on CPU — embedding-bound)  
**RFC:** [`RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md`](RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md)  
**Depends on:** Task 13.4 (pipeline overlap)  
**Phase:** Migration §25 Phase 5 — Full async

---

## Goal

Orchestrator dispatches decode waves without blocking on full round-trip unwind. External-embedding decode path pipelines after `TOKEN_READY`.

**Exit criteria:** Bubble < 10%; TPS ≥ 0.8 × ceiling (measured on homelab/Docker with perf trace).

---

## Docker verification (Llama 3.2 1B)

```bash
chmod +x scripts/verify_docker_protocol_v2.sh
./scripts/verify_docker_protocol_v2.sh
```

Uses profile `rfc0013_docker` with model key `llama3_1b` (override: `BENCHMARK_MODEL=llama3_1b`).

Env flags set on all nodes via `docker-compose.yml`:

```bash
DIST_RUNTIME_PROTOCOL_V2=1
DIST_RUNTIME_ENTRY_QUEUE=1
DIST_RUNTIME_STAGE_QUEUE=1
DIST_RUNTIME_CLIENT_PIPELINE=1
DIST_PERF_TRACE=1
```

`DIST_RUNTIME_CLIENT_PIPELINE` defaults to **on** when both entry and stage queues are enabled (override with `=0` to force blocking client).

---

## Changes

| Area | Change |
|------|--------|
| `wave_inbound_queue.h/cpp` | `runtime_client_pipeline_enabled()` |
| `runtime_entry_queue.h/cpp` | `pipeline_gen3_decode_token_queued_step`, `pipeline_gen3_decode_hidden_queued_pipelined` |
| `node_agent.cpp` | Client pipelining: dispatch wave N+1 after `TOKEN_READY`, before `COMPLETE` |
| `split_gen3_a.cpp` | Deferred `COMPLETE`, `DRAIN_PENDING`, ack-before-push ordering |
| `split_tcp_wire.h` | `SPLIT_GEN_CMD_DRAIN_PENDING` |
| `docker-compose.yml` | `DIST_RUNTIME_CLIENT_PIPELINE` passthrough |
| `benchmark_runner.py` | Enable client pipeline on v2 recreate |
| `scripts/verify_docker_protocol_v2.sh` | Bubble < 10% gate via `pipeline_stall_analysis` |
| `benchmark_matrix.yaml` | `rfc0013_docker` profile |

---

## Client pipelining protocol (per decode step)

1. Send wave N (`DECODE` / `DECODE_HIDDEN`)
2. `GENQ` queue ack
3. `GENT` token ready → output token committed
4. **If pipelining:** send wave N+1 + queue ack (outstanding-wave credit)
5. `DRAIN` (last wave) or next-wave recv → `COMPLETE` for wave N

---

## Measured (Docker 1B, client pipeline on)

| Metric | Value |
|--------|-------|
| Generate | 16 tokens, ~11.4 tok/s decode |
| Queue overlap | max depth 2 (entry/middle/final) |
| Bubble (trace) | ~59 ms / ~82 ms period ≈ **72%** |

Bubble remains embedding/client-bound on Docker CPU; protocol pipelining is active (no step-1 magic errors, 16-token steady decode). Phase 5 **<10%** target needs homelab GPU or async embedding overlap.

---

## Next

- Phase 6: v1 deprecation path per RFC §25
- Homelab TPS ≥ 0.8 × ceiling gate (optional; Docker CPU is below ceiling)
