# Task 11 — Runtime Architecture Refactor (Layer-First Runtime)

**Status:** Planned  
**Depends on:** Task 10 control-plane isolation (orchestrator no longer materializes GGUF)  
**Goal:** Symmetric runtime nodes; planner assigns runtime roles, not implicit entry bottlenecks.

---

## Problem (current state after Task 10)

Pipeline still assumes a special **entry** node:

```
Client → Orchestrator → Entry (tokenizer + embedding + prefill + layers 0–N)
                      → Middle → Final
```

Entry receives disproportionate work:

- Materialize worker GGUF with tokenizer/embedding blobs
- Load tokenizer (`vocab_only`)
- Tokenize every prompt
- Start prefill (highest latency spike)
- All `/session/generate` HTTP traffic

Planner assigns entry to the **highest benchmark score** node → Qwen 30B entry landed on M3 Pro (18 GB RAM) and froze the laptop.

Orchestrator is now light (~264 MB peak for 30B session create), but **data-plane asymmetry** remains.

---

## Target architecture

```
Client
  ↓
Tokenizer Service   (independent role)
  ↓ token ids
Embedding Service   (independent role)
  ↓ hidden
Distributed Runtime (PIPELINE_STAGE nodes — layer range only)
  ↓
Output Service      (norm + lm_head)
  ↓
Sampler Service
  ↓
Client
```

**Properties at completion:**

| Property | Target |
|----------|--------|
| Orchestrator | No GGUF reads; no model format knowledge |
| Runtime | Layer Store + TensorProvider; no full GGUF required for inference |
| Pipeline stages | Symmetric — differ only by layer range + assigned runtime role |
| Planner | Assigns layers **and** runtime roles using cost model |
| New architectures | Descriptor-only — no special-case runtime branches |
| Materializer | Compatibility layer (verify, export, debug) — not on inference hot path |

---

## Current codebase anchors

| Component | Location | Task 11 reuse |
|-----------|----------|---------------|
| `dist_node_role` (entry/middle/final) | `dist_common.h` | Replace / extend with `runtime_role` |
| `worker_role`, `worker_descriptor` | `architecture/semantic_runtime_descriptor.h` | Extend to TOKENIZER, EMBEDDING, etc. |
| `semantic_runtime_descriptor` | architecture/ | Basis for role → blob requirements |
| Layer Store | `node_agent/layer_store/` | Primary truth; feeds TensorProvider (11.7) |
| Materializer | `layer_gguf_assembler`, `worker_builder` | Demote to compat layer (11.8) |
| Hidden transfer | `split_tcp_wire`, workers | Unchanged transport; roles change endpoints |
| Architecture plugins | `architecture/plugins/*` | Source for role descriptors per family |
| Planner v1 | `layer_planner`, `layout_planner` | Score-proportional layers only → Planner v2 (11.5) |

---

## Subtasks

### 11.1 — Runtime Roles

Introduce `runtime_role` distinct from pipeline topology:

```
TOKENIZER | EMBEDDING | PIPELINE_STAGE | OUTPUT_HEAD | SAMPLER
```

Add `runtime_role_descriptor`:

- `supports_tokenizer`, `supports_embedding`, `supports_sampling`
- `preferred_gpu`, `required_memory`, `estimated_compute`

**Tests:** `test-runtime-role-planner`, `test-role-assignment`, `test-role-memory-fit`

**Deliverables:**

- `runtime_role.h` / `runtime_role_descriptor.h`
- Extend `semantic_runtime_descriptor` with role catalog (not just entry/middle/final workers)
- Orchestrator session graph JSON includes role assignments per node

---

### 11.2 — Tokenizer Service

Remove tokenizer from entry worker.

```
Client → Tokenizer Service → token_ids → Pipeline
```

Entry (or any PIPELINE_STAGE) receives **pre-tokenized** ids only.

**Tests:** `test-tokenizer-service`, `test-tokenizer-remote`, `test-tokenizer-parity`

**Migration note:** Task 10 moved tokenize to entry node-agent; 11.2 moves it to a dedicated role (may be same physical node, different process/service).

---

### 11.3 — Embedding Service

Split embedding from layer-0 forward.

```
Embedding Service → hidden → Pipeline (layer range)
```

**Tests:** `test-embedding-service`, `test-hidden-injection`, `test-hidden-parity`

**Existing:** hidden injection APIs (Task 2) — reuse for embedding → stage-0 handoff.

---

### 11.4 — Output Service

Split norm + lm_head + sampling from final worker.

```
Pipeline → Output Service → Sampler → Client
```

**Tests:** `test-output-service`, `test-logits-parity`, `test-sampling-service`

---

### 11.5 — Planner v2

Stop mapping `max(score) → entry`.

Add `runtime_cost_model`:

| Role | Primary cost signal |
|------|---------------------|
| Tokenizer | CPU score, low memory |
| Embedding | Memory bandwidth |
| PIPELINE_STAGE | GPU score, layer memory |
| OUTPUT_HEAD | GPU VRAM |
| Sampler | CPU |

Planner output: **runtime graph** (roles + layer ranges + node assignments).

**Tests:** `test-runtime-cost`, `test-role-balancing`, `test-large-model-placement`

**Policy example (Qwen 30B):**

```
node-c: TOKENIZER + SAMPLER
node-b: EMBEDDING + OUTPUT_HEAD
node-a: PIPELINE_STAGE layers 0–8
node-c: PIPELINE_STAGE layers 8–16
...
```

---

### 11.6 — Session Create Refactor

```
session/create → runtime graph → configure → ready
```

No materialize on session create path. Workers bind to Layer Store / TensorProvider at configure time.

**Tests:** `test-session-runtime`, `test-runtime-restart`, `test-runtime-reconfigure`

**Interim (Task 10):** `POST /runtime/prepare` still materializes GGUF — removed in 11.6+11.7.

---

### 11.7 — Layer Provider API (largest step)

Replace `worker.gguf → llama_load_model()` with:

```cpp
struct TensorProvider {
    bool tensor_exists(name);
    void* mmap_tensor(name);
    bool load_tensor(name, buffer);
    bool verify_tensor(name, checksum);
};
```

Runtime loads tensors directly from Layer Store blobs. **Requires llama.cpp integration** (model loader hooks or custom graph builder).

**Tests:** tensor provider unit tests; integration with one architecture (Llama) first.

---

### 11.8 — Materializer as Compatibility Layer

After 11.7:

- Inference path: **never** calls `materialize_worker_gguf`
- Materializer used by: verification suite, export tools, parity tests, migration

---

### 11.9 — Next-gen Planner

Dynamic role reassignment; multi-node role colocation; rebalance tokenizer/embedding off overloaded nodes.

---

## Dependency graph

```
11.1 Runtime Roles
  ├─→ 11.2 Tokenizer Service
  ├─→ 11.3 Embedding Service
  ├─→ 11.4 Output Service
  └─→ 11.5 Planner v2
        └─→ 11.6 Session Create (no materialize)
              └─→ 11.7 TensorProvider  ← critical path, llama.cpp changes
                    └─→ 11.8 Materializer demotion
                          └─→ 11.9 Planner v3 / dynamic roles
```

**Parallelizable after 11.1:** 11.2, 11.3, 11.4 (separate services, GGUF materialization still OK temporarily).

**Blocking:** 11.6 full completion requires 11.7.

---

## Benchmark matrix (each milestone)

**Small:** TinyLlama, Llama 3.2, Qwen 2.5, Gemma, Phi, SmolLM, DeepSeek  
**Large:** Qwen 8B, Qwen 14B, Qwen 30B

**Metrics:**

- TTFT, Decode TPS
- RAM / VRAM per node
- Network / hidden transfer bytes & latency
- CPU utilization
- Materialization time (until eliminated)
- Session create latency
- Planner time

**Regression gate:** orchestrator peak RSS < 300 MB for any model (Task 10 criterion preserved).

---

## Suggested first PR (11.1)

1. Add `runtime_role` enum + `runtime_role_descriptor` (no behavior change).
2. Extend session create response with `runtime_graph` placeholder (roles unassigned → legacy entry/middle/final mapping).
3. Add `test-runtime-role-planner` with synthetic nodes — assert 30B tokenizer not placed on 18 GB node when 31 GB node available.

This unblocks planner v2 design without touching llama.cpp core yet.

---

## Relationship to Task 10

| Task 10 (done) | Task 11 (next) |
|----------------|----------------|
| Orchestrator doesn't materialize | No node role is special by default |
| Tokenizer on entry node-agent | Tokenizer is its own service/role |
| `POST /runtime/prepare` materializes | Configure binds Layer Store directly |
| Planner: layers only | Planner: layers + roles + cost model |

Task 10 fixed **control-plane OOM**. Task 11 fixes **data-plane bottleneck and architectural asymmetry**.
