# Task 13.2 — Wire Envelope (RFC-0013 Phase 2)

**Status:** Complete  
**RFC:** [`RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md`](RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md)  
**Depends on:** Task 13.1 (WaveID tracing)  
**Phase:** Migration §25 Phase 2 — Wire envelope

---

## Goal

Define Runtime Protocol v2 event framing and version negotiation alongside v1 RPC. **v1 remains the default** inference path until Phase 3.

**Exit criteria:**

- Version negotiation succeeds (v1 default; v2 when `DIST_RUNTIME_PROTOCOL_V2=1` on client and worker)
- v2 `WAVE` envelope encode/decode validated by unit tests
- Generate/decode still uses v1 `pipeline_gen3_send_recv` after handshake

---

## Components

| File | Role |
|------|------|
| `transport/split_wave_wire.h/cpp` | v2 `WAVE` event envelope (WaveID, session_id, sequence, payload) |
| `transport/runtime_protocol.h/cpp` | Negotiation on v1 ctrl channel + v2 PROTO_VERSION/ACK exchange |
| `transport/split_tcp_wire.h` | `SPLIT_GEN_CMD_PROTO_NEGOTIATE = 7`, `split_proto_negotiate_resp` |
| `workers/split_gen3_a.cpp` | Entry worker handles negotiate + v2 handshake |
| `node_agent.cpp` | Optional negotiate after ctrl connect (env-gated) |

---

## Version negotiation (v1 ctrl channel)

Client sends `SPLIT_GEN_CMD_PROTO_NEGOTIATE` with `n_tokens = requested_protocol`.

Server responds `split_proto_negotiate_resp`:

```json
{
  "agreed_protocol": 1,
  "server_max_protocol": 2,
  "status": 0
}
```

`agreed_protocol = min(requested, server_max)`. Server max is `2` only when `DIST_RUNTIME_PROTOCOL_V2=1`.

---

## v2 envelope

```
split_wave_envelope_hdr (64 bytes)
  magic          'WAVE'
  envelope_version
  event_type
  flags
  wave_id
  sequence
  session_id[36]
  payload_len
payload[payload_len]
```

Phase 2 events: `PROTO_VERSION`, `PROTO_ACK`.

---

## Enable v2 negotiation (opt-in)

```bash
export DIST_RUNTIME_PROTOCOL_V2=1   # client (node_agent) and entry worker
```

Without this variable, behavior is unchanged v1 (no negotiate attempt from client).

---

## Tests

```bash
cmake --build build --target test-wave-wire test-runtime-protocol
./build/bin/test-wave-wire
./build/bin/test-runtime-protocol
```

---

## Next phase

**Task 13.3 — Entry queue:** async wave enqueue at entry stage (RFC §25 Phase 3).
