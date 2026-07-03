# Orchestrator Runtime Boundary

**Status:** Phase 1 complete — control plane isolated from runtime materialization  
**Goal:** Orchestrator peak RSS stays < 300 MB regardless of model size.

## Confirmed root cause (Phase 0)

Qwen3-30B OOM during `POST /session/create`:

```
session_create enter     219 MB
resolve_tokenizer enter  219 MB
materialize_gguf_before  224 MB
→ Killed (OOM)
```

**Culprit:** `layer_store_materialize_gguf()` inside `resolve_tokenizer_gguf_path()` on the orchestrator host.

## Architecture (after refactor)

```
Client → Orchestrator (control plane)
           manifest / layout / coverage / install plan / scheduler
           POST /session/create → setup_pipeline()
             → POST /runtime/prepare  (each node)
             → POST /configure        (skip_materialize + worker_gguf)
         → POST /session/generate
             → entry node /pipeline/generate (prompt text)
               → tokenize → workers → detokenize

Node Agent (data plane)
  Layer Store → Materializer → worker GGUF → tokenizer (entry) → workers
```

## Orchestrator — allowed

- Registry, manifest, layout, coverage, install plan, scheduler, session registry
- REST coordination (`prepare_runtime_node`, `configure_node`, `run_generation` proxy)
- Metadata-only memory estimates (`estimate_model_memory_from_manifest`)
- Metadata cache (`layer_store_cache_metadata` — HTTP range of header bytes only)

## Orchestrator — forbidden (removed)

- `layer_store_materialize_gguf()`
- `llama_model_load_from_file` / tokenizer
- `split_gen_tokenize` / `split_gen_token_text`
- `ggml_backend_load_all()`
- Full GGUF file load for memory estimation
- Verification pipeline (use standalone `materialization_verify` tool)

## Node Agent — new responsibilities

| Endpoint | Purpose |
|----------|---------|
| `POST /runtime/prepare` | Materialize worker GGUF + init entry tokenizer |
| `POST /configure` | Spawn workers (`skip_materialize=true` + `worker_gguf`) |
| `POST /pipeline/generate` | Accept `prompt` (text) or `prompt_tokens`; tokenize on entry |

## RSS diagnostics

Stderr per stage (`orchestrator: RSS stage=…`) and `GET /debug/rss`.

Key stages: `register`, `discover`, `manifest`, `layout`, `install plan`, `coverage`, `prepare_runtime`, `session_create`, `generate`.

## Verification & export

- **Verification:** unchanged — use `materialization_verify` and other verify tools (not orchestrator HTTP).
- **Full GGUF export:** use Layer Store + Materializer via verify/export tools (or future `POST /models/{id}/export-gguf` on node-agent).

## Deploy & validate

```bash
cmake --build build --target orchestrator node_agent -j8
# deploy to cluster, restart orchestrator + nodes

python3 benchmarks/benchmark_runner.py --profile warm_ext --model qwen30b \
  --cluster-size 3 --infra-only 2>&1 | tee logs/qwen30b_rss_trace.log
```

**Pass criteria:** session_create succeeds; orchestrator RSS stays ~200 MB; generate succeeds.
