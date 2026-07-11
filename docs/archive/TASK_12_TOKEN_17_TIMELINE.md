# Task 12.2 — Token 17 Timeline Reconstruction

**Trace:** `trace-000004`  
**Session ordinal:** 16 (0-based entry receive index)  
**Wall period** (entry recv N → entry recv N+1): **65.96 ms**  

## Step-by-step (non-aggregated)

| Step | t+ms | dur ms | Actor | Action | Initiated by | Waited on |
|------|-----:|-------:|-------|--------|--------------|-----------|
| `orchestrator_send` | 0.00 | 0.00 | orchestrator | pipeline_gen3_send_req(DECODE, token 17) | orchestrator | token 16 full round-trip | <!-- No per-token orchestrator span in trace; timestamp = entry ENTRY_RECEIVE (request arrived). -->
| `entry_recv` | 0.00 | 0.00 | entry | ENTRY_RECEIVE on ctrl socket | orchestrator | orchestrator send (blocked in split_gen_recv_req) |
| `entry_compute` | 0.04 | 23.90 | entry | ENTRY_COMPUTE_BEGIN → ENTRY_COMPUTE_END (GGML decode) | entry recv | — |
| `entry_send` | 23.94 | 0.08 | entry | Forward hidden state → middle (TCP ab) | entry compute complete | entry compute | <!-- Inferred at COMPUTE_END; no post-compute ENTRY_SEND in trace window. -->
| `middle_recv` | 6.98 | 0.00 | middle | MIDDLE_RECEIVE hidden tensor | entry send | prior pipeline wave (tagged same token_idx) | <!-- Receive timestamp precedes entry compute end — pipeline lag / token_idx tags prior wave. -->
| `middle_compute` | 7.00 | 16.88 | middle | MIDDLE_COMPUTE_BEGIN → MIDDLE_COMPUTE_END | middle recv | — |
| `middle_send` | 23.88 | 0.08 | middle | Forward hidden → final (TCP bc) | middle compute complete | middle compute | <!-- Inferred at MIDDLE_COMPUTE_END. -->
| `final_recv` | 15.15 | 0.00 | final | FINAL_RECEIVE hidden tensor | middle send | middle forward |
| `final_compute` | 15.19 | 8.51 | final | FINAL_COMPUTE_BEGIN → FINAL_COMPUTE_END | final recv | — |
| `final_sample` | 23.65 | 0.06 | final | SAMPLER_END (next token id) | final compute | final compute |
| `orchestrator_recv` | 23.70 | 42.25 | orchestrator | pipeline_gen3_recv_a_resp returns token to node_agent loop | final pipeline completion | entry worker forwarding response | <!-- End inferred before next ENTRY_RECEIVE; duration = bubble until token N+1 dispatch. -->
| `orchestrator_send_next` | 65.96 | 0.00 | orchestrator | pipeline_gen3_send_req(DECODE, token 18) | orchestrator | token 17 response |

## Causal chain (protocol order)

```
orchestrator send
  ↓
entry recv
  ↓
entry compute
  ↓
entry send
  ↓
middle recv → middle compute → middle send
  ↓
final recv → final compute → final sample
  ↓
orchestrator receives (bubble until next dispatch)
  ↓
orchestrator send token 18
```

## Occupancy (0 = entry recv token N, width = period until token N+1)

```
Period: 66.0ms                                        
Entry        |████████████████████▶                                   |
Middle       |     ███████████████▶                                   |
Final        |            ████████░                                   |
Orchestrator ||                   ···································||
             0                           66ms
```

## Transitions (who initiated / who waited)

| From → To | gap ms | Initiated by | Waited |
|-----------|-------:|--------------|--------|
| `orchestrator_send` → `entry_recv` | 0.00 | orchestrator | entry waited on orchestrator send (blocked in split_gen_recv_req) |
| `entry_recv` → `entry_compute` | 0.04 | entry recv | entry waited on — |
| `entry_compute` → `entry_send` | 0.00 | entry compute complete | entry waited on entry compute |
| `entry_send` → `middle_recv` | 0.00 | entry send | middle waited on prior pipeline wave (tagged same token_idx) |
| `middle_recv` → `middle_compute` | 0.03 | middle recv | middle waited on — |
| `middle_compute` → `middle_send` | 0.00 | middle compute complete | middle waited on middle compute |
| `middle_send` → `final_recv` | 0.00 | middle send | final waited on middle forward |
| `final_recv` → `final_compute` | 0.04 | final recv | final waited on — |
| `final_compute` → `final_sample` | 0.00 | final compute | final waited on final compute |
| `final_sample` → `orchestrator_recv` | 0.00 | final pipeline completion | orchestrator waited on entry worker forwarding response |
| `orchestrator_recv` → `orchestrator_send_next` | 0.00 | orchestrator | orchestrator waited on token 17 response |

## Mermaid (wall-clock, ms from entry recv token 17)

```mermaid
gantt
    dateFormat X
    axisFormat %L
    title Token 17 pipeline (trace-000004, Docker)

    section orchestrator
    orchestrator send : 0.0, 0.0
    section entry
    entry compute : 0.0, 23.9
    section entry
    entry send : 23.9, 24.0
    section middle
    middle compute : 7.0, 23.9
    section middle
    middle send : 23.9, 24.0
    section final
    final compute : 15.2, 23.7
    section final
    final sample : 23.6, 23.7
    section orchestrator
    orchestrator recv : 23.7, 66.0
    section orchestrator
    orchestrator send next : 66.0, 66.0
```

## Bubble accounting

- **Critical path** (entry recv → final sample): **23.70 ms**
- **Orchestrator+entry bubble** (pipeline done → next dispatch): **42.25 ms**
- **Entry period** (recv N → recv N+1): **65.96 ms**
- Bubble share: **64.1%** of period
- Sum of stage compute spans (overlapped, not additive): **49.35 ms**

## Interpretation

1. **Who holds the ~42 ms bubble?** Orchestrator thread in `pipeline_gen3_send_recv` after final response is ready — workers are idle; next token cannot be sent until recv completes.
2. **Can token 18 be dispatched earlier?** **No** under current protocol: one blocking RPC per token on the orchestrator↔entry ctrl socket (`node_agent.cpp` serial decode loop).
3. **Stages overlap in wall time** — middle recv at +7 ms is a *prior wave* (pipeline lag); entry compute for token 17 still runs until +24 ms. Align by receive **ordinal**, not `token_idx` alone.
4. **Network is not the stall** — hidden hops are < 0.1 ms; bubble is protocol/orchestrator coupling.

## Reproduce

```bash
PYTHONPATH=benchmarks python3 benchmarks/perf_trace/token_timeline.py \
  logs/perf_trace/docker_verify_20260707_151625/raw --trace trace-000004 --token 17 \
  -o logs/perf_trace/docker_verify_20260707_151625/analysis/token_17_timeline.json \
  --md docs/TASK_12_TOKEN_17_TIMELINE.md
```
