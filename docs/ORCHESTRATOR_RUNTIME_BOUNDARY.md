# Orchestrator Runtime Boundary — Diagnostic TZ

**Status:** Phase 0 (diagnostics only — no runtime removal yet)  
**Goal:** Prove where OOM (`Killed`) comes from before refactoring.

## Symptom

Homelab orchestrator exits with shell message `Killed` when benchmark reaches **Qwen3-30B** (also observed after **Qwen3-14B** install ~10 min). Classic **OOM killer** signature — not HTTP 503 from nodes.

Cluster nodes stay healthy; failure is on the **control-plane host** running `orchestrator`.

---

## Target Architecture (user requirement)

```
Client → Orchestrator (thin)     → Node Agent → Worker → llama runtime
         manifest / layout / sync
         REST / scheduler
         NO llama_model / decode / KV / materializer on workers
```

**RSS budget:** any model (30B / 70B / 120B) must increase orchestrator RSS by **< 50 MB**.

---

## Include Audit (`orchestrator.cpp` and linked objects)

| Include | File | Purpose | Verdict |
|---------|------|---------|---------|
| `llama.h` | orchestrator.cpp | tokenizer load, tokenize, detokenize | **Must move** |
| `ggml-backend.h` | orchestrator.cpp | `ggml_backend_load_all()` in `/session/generate` | **Must move** |
| `split_gen_common.h` | orchestrator.cpp | `split_gen_tokenize`, `split_gen_token_text` | **Must move** |
| `split_tcp_wire.h` | orchestrator.cpp | dead `gen3_send_recv` (unused) | **Remove** |
| `layer_store/*.h` | orchestrator.cpp | metadata cache + **GGUF materialize on orchestrator** | **Partial OK / materialize Must move** |
| `gguf.h` | manifest_builder.cpp | metadata-only GGUF parse (`no_alloc`) | **OK** |
| `llama.h` | memory_estimator.cpp (linked) | **full** `llama_model_load_from_file` fallback | **Must move or metadata-only** |

Orchestrator **CMake** links `llama`, `llama-common`, and compiles:

- `layer_gguf_assembler.cpp`, `worker_builder.cpp`, `descriptor_materialize.cpp` — worker GGUF assembly (tensor fetch over HTTP)

Subdirs under `orchestrator/` (layout, coverage, install_planner, registry) — **no direct llama calls** ✓

---

## Runtime Boundary Audit

### Must move to node-agent (inference / runtime)

| Symbol / call | Location | What it does |
|---------------|----------|--------------|
| `llama_model_load_from_file` | `session_get_tokenizer()` ~L110 | Loads **full GGUF** into orchestrator RAM for tokenizer |
| `llama_model_free` | `session_free_tokenizer()` | Paired with above |
| `llama_model_get_vocab` + `split_gen_tokenize` | `/session/generate` ~L2346 | Prompt → token IDs on orchestrator |
| `split_gen_token_text` | `/session/generate` ~L2368 | Token IDs → text on orchestrator |
| `ggml_backend_load_all()` | `/session/generate` ~L2334 | Loads all GGML backends on control plane |
| `fetch_entry_worker_tokenizer()` | `resolve_tokenizer_gguf_path()` ~L1132 | **Streams full entry worker GGUF** from node-a to orchestrator disk |
| `layer_store_materialize_gguf()` | `resolve_tokenizer_gguf_path()` ~L1222 | Assembles GGUF on orchestrator (HTTP range fetch of tensors) |
| `estimate_model_memory(path)` | `get_model_memory_for_record()` ~L1069 | Full `llama_model_load` when manifest missing |
| `gen3_send_recv` | ~L891 | Dead code — direct split_gen wire protocol (never called) |

### OK on orchestrator (control plane)

| Area | Examples |
|------|----------|
| GGUF metadata | `manifest_builder`: `gguf_init_from_file` with `no_alloc=true` |
| Manifest / layer descriptors | registry, manifest JSON |
| Layout / planner | `build_desired_layout`, `dist_plan_layers_memory_aware` |
| Coverage / install plan | coverage.cpp, install_planner.cpp |
| Node HTTP proxy | `configure_node()`, `setup_pipeline()` → POST `/configure` on **nodes** |
| `run_generation()` | Proxies **pre-tokenized** `prompt_tokens` to entry `/pipeline/generate` ✓ |
| Metadata cache | `layer_store_cache_metadata()` — fetches **metadata bytes only** (~MB) |

### setup_pipeline / configure — clarification

`setup_pipeline()` does **not** run llama inference. It:

1. Computes TCP port plan
2. `shutdown_node()` + POST `/configure` on each node (worker spawn happens **on node**)

This is allowed coordination. Name is confusing but behavior is OK.

---

## Hypothesis: Qwen30B OOM call chain

Most likely stack (to confirm with RSS logs):

```
POST /session/create (qwen3-30b)
  → get_model_memory_for_record()        [OK if manifest exists]
  → resolve_tokenizer_gguf_path()
       → fetch_entry_worker_tokenizer()  [multi-GB disk + buffer]
       OR layer_store_materialize_gguf() [multi-GB HTTP tensor assembly]
  → (later) POST /session/generate
       → ggml_backend_load_all()
       → llama_model_load_from_file()   [multi-GB RAM — primary suspect]
       → split_gen_tokenize()
```

Secondary suspect if manifest missing locally:

```
POST /models/{id}/layout
  → estimate_model_memory(local_gguf) → llama_model_load_from_file(full file)
```

---

## Phase 0: RSS Diagnostics (implemented)

### Orchestrator logging

After rebuild, stderr lines:

```
orchestrator: RSS stage=session_create enter model=qwen3-30b rss=42.1 MB baseline_delta=+0.3 MB
orchestrator: RSS stage=tokenizer_load_before model=qwen3-30b rss=42.1 MB baseline_delta=+0.3 MB
orchestrator: RSS stage=tokenizer_load_after model=qwen3-30b rss=18432.5 MB baseline_delta=+18390.2 MB
orchestrator: RSS stage=session_create leave model=qwen3-30b rss=18432.5 MB scope_delta=+18390.0 MB
```

### HTTP probe

```bash
curl -s http://192.168.50.154:9000/debug/rss | jq
```

### Benchmark sampling

`benchmark_perf.py` records `infra.orchestrator_rss` with `peak_rss_mb`, `avg_rss_mb`, per-stage samples when `/debug/rss` is available.

---

## Repro procedure (Qwen 30B memory trace)

1. Rebuild & deploy orchestrator with RSS probes:
   ```bash
   cmake --build build --target orchestrator -j8
   # restart on homelab
   ```

2. Run infra-only path for qwen30b:
   ```bash
   export ORCHESTRATOR=http://192.168.50.154:9000
   python3 benchmarks/benchmark_runner.py --profile warm_ext --model qwen30b \
     --cluster-size 3 --infra-only 2>&1 | tee logs/qwen30b_rss_trace.log
   ```

3. Watch orchestrator stderr for largest `scope_delta` / `baseline_delta`.

4. Record in this doc:
   - Stage with max RSS jump
   - Exact function (from log stage name)
   - RSS before/after

**Pass criteria for Phase 0:** identify single dominant allocation with evidence (log line + delta > 1 GB).

---

## Phase 1: Refactor plan (after diagnosis confirmed)

1. **Remove** `llama.h` from orchestrator; split `memory_estimator` into metadata vs full-load paths.
2. **Move tokenize/detokenize** to entry node (`POST /pipeline/generate` accepts `prompt` string OR token IDs).
3. **Remove** `fetch_entry_worker_tokenizer` / `layer_store_materialize_gguf` from orchestrator.
4. **Remove** linked `worker_builder`, `layer_gguf_assembler` from orchestrator target (keep on node_agent).
5. **Unlink** `llama` from orchestrator CMake target.
6. Re-run Qwen30B / Gemma27B / Llama70B — assert peak RSS delta < 50 MB.

---

## Benchmark metrics (added)

| Field | Source |
|-------|--------|
| `orchestrator_rss.peak_rss_mb` | max over Phase A stages |
| `orchestrator_rss.avg_rss_mb` | mean sample |
| `orchestrator_rss.peak_baseline_delta_mb` | max vs startup baseline |
| Per-stage samples | register … session_create |

---

## Current benchmark state (context)

| Run | Models | Notes |
|-----|--------|-------|
| `20260703_111857` | tinyllama, llama3_1b, qwen2_1_5b | 360/360 OK |
| `20260703_112852` | +6 ext models | phi3_5 0/120 (503); qwen30b/gemma27b incomplete (orchestrator OOM) |

**Do not resume large-model benchmark until orchestrator RSS fix or Phase 0 trace confirms root cause.**
