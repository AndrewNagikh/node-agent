# Task 12 — Distributed Runtime Performance Profiler

**Status:** Plan  
**Date:** 2026-07-06 (rev. 2)  
**Depends on:** Task 11 (layer-first runtime, stable generate path)  
**Feeds into:** Task 13 (targeted optimizations)

---

## 1. Цель

После Task 12 команда должна **измерением**, без изменения inference, отвечать на вопросы:

| Вопрос | Ответ из отчёта |
|--------|-----------------|
| Почему 3 tok/s, а не 120? | Mono vs Dist breakdown, ms/token по категориям |
| Где теряется время? | Per-token timeline + bottleneck % |
| Какая нода простаивает? | Entry/Middle/Final utilization % |
| Какой bottleneck? | Critical path + ranked categories |
| Сеть / сериализация / scheduler / GGML / compute? | Отдельные bucket-метрики |
| Эффект оптимизации X? | Baseline trace + regression diff |
| Install 300–1000s — почему? | Install timeline + layer store reuse |
| Session create 60s — где? | Session create span breakdown |

Task 12 охватывает **три независимых surface area**:

1. **Install** — sync, download, verify, layer store (сейчас главный wall-clock bottleneck)
2. **Session / TTFT** — configure, worker startup, prefill, first token
3. **Decode** — per-token pipeline (compute, network, idle)

---

## 2. Performance Targets (Budget)

Task 12 **не улучшает** производительность. Но все измерения должны позволить определить, **почему не достигнуты** следующие цели — и маркировать отклонения в отчёте как `PASS` / `WARN` / `FAIL` относительно budget.

| Metric | Target | Scope | Homelab baseline (2026-07-06) |
|--------|--------|-------|-------------------------------|
| Warm session create | ≤ 1 s | повторный create, blobs READY | 8–62 s ❌ |
| Decode overhead vs mono | ≤ 20% | ms/token decode only | ~900%+ ❌ |
| Hidden transfer | ≤ 1 ms / hop | per hidden, LAN | TBD |
| Serialization | ≤ 0.5 ms | per hop | TBD |
| Scheduler wait | ≤ 1 ms | per token per stage | TBD |
| Worker idle | ≤ 10% | per stage wall time | ~58–78% (est.) ❌ |
| Pipeline utilization | ≥ 90% | entry+middle+final compute/wall | ~22–42% ❌ |
| Install reuse at READY | 100% | blobs_reused / total ops | частично ❌ |
| TTFT (warm) | ≤ 2 s | session ready → first token | TBD |
| Unknown time bucket | ≤ 5% | см. §16 Task 13 readiness | — |

**Правило отчёта:** каждая метрика выводится как `value / target (delta%)`. Без budget цифры бессмысленны — «8 tok/s» может быть отлично или катастрофой в зависимости от GPU util.

---

## 3. Non-Goals (жёсткие ограничения)

Profiler **пассивный**. Запрещено в scope Task 12:

- менять planner, descriptor, scheduler, pipeline topology
- менять layer store, runtime graph, inference hot path
- добавлять batching, overlap, compression, micro-batching
- «оптимизировать по ходу» — только hooks + export + analysis

Когда `DIST_PERF_TRACE=0` (default) — **нулевой overhead**: нет аллокаций trace buffer, нет flush, нет polling.

---

## 4. TTFT ≠ Decode (обязательное разделение)

**Критическая проблема текущих benchmark'ов:** один `tokens_per_sec` смешивает prefill, session setup и decode. Task 12 запрещает объединять их в одну метрику.

### 4.1 Две фазы — два timeline

| Phase | Что измеряем | Оптимизируется |
|-------|--------------|----------------|
| **TTFT** | Session → Configure → Worker READY → Prefill → First Token | Session create, worker startup, model load, prefill graph |
| **Decode** | Token 1 → Token 2 → … → Token N | Pipeline overlap, network, idle, per-hop latency |

### 4.2 TTFT Timeline

```
Session request
  ↓
Resolve layout          (planner)
  ↓
Configure node A/B/C    (per-node spans)
  ↓
Worker startup          (spawn + bind)
  ↓
Model load              (layer store / runtime load)
  ↓
READY wait              (worker_states → READY)
  ↓
Prefill                 (batch, all pipeline stages)
  ↓
First Token             (CLIENT_TTFT)
```

Trace: `phase=ttft`, `token_idx=-1`. Отдельный `ttft.json` + `ttft.csv`.

### 4.3 Decode Timeline

```
Token 0 (first decode step after prefill)
  ↓
Entry: RECEIVE → COMPUTE → SERIALIZE → SEND
  ↓
Middle: RECEIVE → COMPUTE → SERIALIZE → SEND
  ↓
Final: RECEIVE → COMPUTE → SAMPLE
  ↓
Token 1
  ↓
...
```

Trace: `phase=decode`, `token_idx=0..N-1`. Отдельный `tokens.csv` + `timeline.html`.

### 4.4 Отчётные метрики (раздельно)

```
TTFT .............. 12.4 s   (budget ≤ 2 s warm)  FAIL
Decode ............ 115 ms/tok (budget ≤ +20% mono) FAIL
E2E (derived) ..... 3.1 tok/s  ← только справочно, не для оптимизации
```

`E2E tok/s` остаётся в benchmark для регрессий, но **не используется** для bottleneck analysis.

---

## 5. Install Profiler

Homelab benchmark показал **install 300–1000 s** — это нельзя оставить за рамками Task 12.

### 5.1 Install Timeline

```
Register
  ↓
Discover / Manifest
  ↓
Planner (layout)
  ↓
Install plan
  ↓
Download              (per-blob, per-node)
  ↓
Verify / Checksum
  ↓
Store write           (layer store persist)
  ↓
Coverage refresh
  ↓
Reconcile
  ↓
READY
```

Каждый шаг — span с `dur_us`, `trace_id=install-{run_id}-{model}`.

### 5.2 Layer Store Reuse (новая категория)

Отдельный bucket **INSTALL_REUSE**, не смешивать с runtime:

| Sub-category | Event | Meaning |
|--------------|-------|---------|
| `cache_hit` | blob already local, checksum OK | 0 download |
| `cache_miss` | blob missing | full download |
| `reuse` | `blobs_reused` from install job | skip download |
| `download` | bytes transferred | network bound |
| `verify` | checksum / hash verify | CPU bound |
| `repair` | `blobs_repaired` | partial re-fetch |

Attrs per operation:

```json
{
  "event": "INSTALL_BLOB",
  "category": "INSTALL_REUSE",
  "sub": "reuse",
  "blob_id": "layer:12",
  "node_id": "node-b",
  "bytes": 0,
  "dur_us": 4200
}
```

**Budget:** при `coverage.state=READY` повторный install → `reuse` = 100% ops, `download` = 0 bytes.

### 5.3 Install output

```
logs/perf_trace/<run_id>/install/
  install.jsonl       # raw events
  install.json        # merged timeline
  install.csv         # per-blob / per-stage flat
  install_reuse.json  # hit/miss/reuse summary
```

---

## 6. Session Create Profiler

Не агрегировать «Session create 5 s». Разбивать на spans:

| Span | Source | Example |
|------|--------|---------|
| `SESSION_RESOLVE_LAYOUT` | orchestrator | 120 ms |
| `SESSION_CONFIGURE_NODE` | per node_id | A: 230 ms, B: 190 ms, C: 210 ms |
| `SESSION_PREPARE_RUNTIME` | per node, per stage | materialize/bind |
| `SESSION_WORKER_STARTUP` | spawn + IPC | 1800 ms |
| `SESSION_MODEL_LOAD` | runtime_load_count window | 2700 ms |
| `SESSION_READY_WAIT` | `wait_node_worker_ready` poll | 800 ms |
| `SESSION_SERVICE_CONFIGURE` | tokenizer/embedding/output | per role |

Trace: `phase=session_create`, привязка к `session_id`. События из `setup_runtime_graph()` в orchestrator + `/configure` timing на node_agent.

**Warm vs cold:** второй create той же модели (blobs READY) — отдельный budget ≤ 1 s.

---

## 7. Planner Metrics

Profiler записывает **решение** planner и **факт** после run (для валидации модели):

```json
{
  "event": "PLANNER_DECISION",
  "model_id": "qwen3-8b",
  "node_scores": { "node-a": 137.9, "node-b": 120.1, "node-c": 245.0 },
  "chosen_layout": [
    { "node_id": "node-a", "layers": [0, 14) },
    { "node_id": "node-c", "layers": [14, 28) },
    { "node_id": "node-b", "layers": [28, 36) }
  ],
  "predicted_memory_gb": 9.5,
  "predicted_latency_ms_per_token": 12.0,
  "actual_decode_ms_per_token": 115.2,
  "prediction_error_pct": 860
}
```

Позволяет ответить: «planner предсказал 12 ms/tok, факт 115 ms — модель cost неверна».

Sources: `dist_plan_layers_memory_aware`, `runtime_cost_model`, post-run decode trace.

---

## 8. Decode Profiler (Runtime)

### 8.1 Текущее состояние (baseline)

| Компонент | Что есть | Чего не хватает |
|-----------|----------|-----------------|
| `trace_recorder` | JSONL: step_begin, hidden, transport | trace_id, µs, phase split |
| `hidden_transport_trace` | serialize/send/receive | Budget compare, per-hop report |
| `split_gen3_{a,b,c}` | ggml_time_us compute | wait/idle/queue depth |
| `benchmark_overhead.py` | placeholder breakdown | Real spans |

### 8.2 Event model (decode path)

Clock: **только** `steady_clock` / `ggml_time_us()`, timestamps **`ts_us`**. Запрещён wall clock в perf events.

Envelope:

```json
{
  "trace_id": "trace-000042",
  "phase": "decode",
  "token_idx": 57,
  "stage": "entry",
  "node_id": "node-a",
  "event": "ENTRY_COMPUTE_BEGIN",
  "ts_us": 18420391,
  "dur_us": null,
  "category": "COMPUTE",
  "attrs": {}
}
```

Минимальный enum: `ENTRY_*`, `MIDDLE_*`, `FINAL_*`, `SAMPLER_*`, `HIDDEN_TRANSFER`, `CLIENT_RESPONSE` — см. rev.1 spec.

### 8.3 Compute vs Wait vs Idle vs Unknown

```
wall_us = compute + wait + network + serialize + sampling + unknown
idle_us = wait - network_receive_wait   (pipeline bubble)
```

**Unknown** — любой gap без matching event. Task 13 gate: unknown ≤ 5%.

### 8.4 Queue Depth

На каждый decode token записывать глубину очереди worker:

```json
{
  "event": "QUEUE_DEPTH",
  "stage": "middle",
  "token_idx": 12,
  "depth": 2,
  "ts_us": ...
}
```

CSV column: `middle_queue_depth` per token. Паттерн `0,0,1,0,2,1` сразу показывает: pipeline идёт волнами или простаивает.

Implementation: counter в worker cmd loop (pending requests between recv and dispatch).

### 8.5 GPU Utilization

Обязательно, где платформа позволяет:

| Backend | Metric | Source |
|---------|--------|--------|
| **CUDA** | GPU util % | `nvmlDeviceGetUtilizationRates` (node-c Windows/Linux) |
| **Metal** | GPU busy % | `IOReport` / `commandBuffer completed` ratio / MPS counters |
| **CPU fallback** | compute thread busy % | wall - idle на worker thread |

Poll interval: `DIST_PERF_GPU_POLL_MS=100` (вместе с MEM_SAMPLE).

```json
{
  "event": "GPU_SAMPLE",
  "node_id": "node-c",
  "backend": "cuda",
  "gpu_util_pct": 15.2,
  "gpu_mem_used_mb": 11240,
  "ts_us": ...
}
```

**Без GPU util нельзя интерпретировать tok/s:** 8 tok/s при 15% GPU → scheduling/idle bottleneck; при 99% → compute bound.

### 8.6 GGML & Scheduler sub-spans

Optional (`DIST_PERF_TRACE_GGML=1`):

- `GGML_GRAPH_BUILD`, `GGML_GRAPH_EXECUTE`, `GGML_BACKEND_SYNC`
- `SCHED_QUEUE_WAIT`, `SCHED_MUTEX_WAIT`, `SCHED_CV_WAIT`

---

## 9. Архитектура

```
benchmark_runner --profile-runtime
  ├─ DIST_PERF_TRACE=1
  ├─ install trace (sync job events)
  ├─ session_create trace
  ├─ ttft trace (prefill → first token)
  ├─ decode trace (per-token)
  ├─ mono compare
  └─ regression diff vs previous run
        │
        ▼
  logs/perf_trace/<run_id>/
    install/   session/   ttft/   decode/
        │
        ▼
  benchmarks/perf_trace/
    merge.py | bottleneck.py | regression.py | html_timeline.py
        │
        ▼
  report.md + diff.md + timeline.html
```

### 9.1 Trace ID propagation

| Trace kind | ID format | Propagation |
|------------|-----------|-------------|
| Install | `install-{run_id}-{model}` | orchestrator install job |
| Session | `session-{session_id}` | orchestrator → nodes |
| Generate | `trace-{seq:06d}` | orchestrator → entry → middle → final |

### 9.2 Включение

| Env | Scope |
|-----|-------|
| `DIST_PERF_TRACE=1` | Master switch |
| `DIST_PERF_TRACE_DIR` | Output root |
| `DIST_PERF_TRACE_TOKENS=N` | Decode cap (CI: 32) |
| `DIST_PERF_MEM_POLL_MS=100` | RSS / KV poll |
| `DIST_PERF_GPU_POLL_MS=100` | GPU util poll |

```bash
python benchmarks/benchmark_runner.py --profile homelab_full --profile-runtime
```

---

## 10. Regression Detection

После **каждого** benchmark с `--profile-runtime` автоматически:

```
previous benchmark (results.json + perf_trace/)
        ↓
current benchmark
        ↓
regression_diff.json + regression.md
```

Пример diff:

| Metric | Prev | Curr | Delta |
|--------|------|------|-------|
| Entry compute | 7.9 ms | 8.1 ms | **+2%** |
| Middle idle | 29.8 ms | 19.4 ms | **-35%** ✓ |
| Network / hop | 0.7 ms | 0.71 ms | +1% |
| Install total | 850 s | 170 s | **-80%** ✓ |
| TTFT warm | 12.4 s | 4.9 s | **-60%** ✓ |
| Decode ms/tok | 115 ms | 136 ms | **+18%** ✗ |

Implementation: `benchmarks/perf_trace/regression.py`

- Key: `(profile, model, cluster_size)`
- Store: `logs/perf_trace/_baselines/<profile>_<model>.json`
- Flags: `REGRESSION_WARN_PCT=10`, `REGRESSION_FAIL_PCT=25`
- CI: fail if decode or TTFT regression > threshold without documented reason

**Иначе через месяц никто не вспомнит, что именно ускорилось.**

---

## 11. Post-Processor (Python)

Пакет `benchmarks/perf_trace/`:

| Module | Responsibility |
|--------|----------------|
| `merge.py` | JSONL → unified trace per phase |
| `install.py` | Install timeline + reuse summary |
| `session.py` | Session create span breakdown |
| `ttft.py` | TTFT timeline |
| `token_csv.py` | Per-token decode flat |
| `bottleneck.py` | Category %, budget PASS/WARN/FAIL |
| `critical_path.py` | Longest path per token |
| `utilization.py` | Pipeline + GPU util |
| `queue.py` | Queue depth stats |
| `planner.py` | Predicted vs actual |
| `mono_compare.py` | Mono vs dist decode (not TTFT mix) |
| `regression.py` | Prev vs curr diff |
| `html_timeline.py` | TTFT + decode Gantt, GPU overlay |

### 11.1 Bottleneck report

```
Compute .............. 22%
Network .............. 3%
Serialization ........ 2%
Idle ................. 70%
Unknown .............. 3%    ← must be ≤ 5%
```

### 11.2 Output layout

```
logs/perf_trace/<run_id>/
  install/     install.json, install.csv, install_reuse.json
  session/     session.json
  ttft/        ttft.json, ttft.csv
  decode/      trace.json, tokens.csv, queue.csv
  analysis/    bottleneck.json, critical_path.json, planner.json
               mono_vs_dist.json, regression_diff.json
  timeline.html
  report.md
```

---

## 12. Monolithic Comparison

Тот же prompt, seed, `n_ctx`, `max_tokens`:

1. **Mono:** `benchmark_monolithic` — decode ms/token only
2. **Dist:** decode trace — **без TTFT и session**

```
MONO decode ........ 8.3 ms/tok  (120.5 tok/s)
DIST decode ........ 115.2 ms/tok (8.7 tok/s)
Overhead ........... +1287% (budget ≤ +20%)  FAIL

Breakdown why:
  Idle 78% | Compute 22% | Network 2%
```

---

## 13. Benchmark Integration

### 13.1 Profile `runtime_profile`

```yaml
runtime_profile:
  mode: homelab
  models: [tinyllama, smollm2_1_7b, qwen8b]
  perf_trace: true
  perf_trace_install: true
  perf_trace_session: true
  perf_trace_ttft: true
  perf_trace_decode: true
  mono_compare: true
  regression_baseline: true
```

### 13.2 Runner

- `--profile-runtime` → all trace phases + regression diff
- `benchmark_report.py` → budget columns (TTFT, Decode, Install reuse)
- Replace `estimate_decode_breakdown` placeholder with real `bottleneck.json`

---

## 14. Implementation Phases

| Phase | Scope | Days |
|-------|-------|------|
| **12.1** | Schema, perf_recorder, trace_id, phase field | 3–4 |
| **12.2** | Install profiler + layer store reuse events | 4–5 |
| **12.3** | Session create span instrumentation | 3–4 |
| **12.4** | TTFT timeline (separate from decode) | 3–4 |
| **12.5** | Decode worker events + queue depth | 5–7 |
| **12.6** | GPU util polling (CUDA + Metal) | 3–4 |
| **12.7** | GGML/scheduler sub-spans (optional flag) | 3–4 |
| **12.8** | Python merge + bottleneck + budget | 5–6 |
| **12.9** | Regression detection | 2–3 |
| **12.10** | HTML timeline (TTFT + decode + GPU) | 3–4 |
| **12.11** | Benchmark wire-up + homelab validation | 3–4 |

**Total:** ~5–6 weeks.

### Spike (immediate)

Tinyllama, 8 decode tokens:

1. `trace_id` + `phase=decode`
2. `ENTRY_COMPUTE` + `HIDDEN_TRANSFER` + one `QUEUE_DEPTH`
3. Merge → `tokens.csv` row with budget compare

---

## 15. Acceptance Criteria

| # | Criterion |
|---|-----------|
| 1 | TTFT и Decode — **раздельные** timeline и метрики |
| 2 | Install timeline с reuse/hit/miss/download/verify |
| 3 | Session create — span breakdown (не один aggregate) |
| 4 | Decode: timeline любого token |
| 5 | Queue depth per token per stage |
| 6 | GPU util % в отчёте (CUDA + Metal где доступно) |
| 7 | Planner: predicted vs actual latency |
| 8 | Budget: каждая метрика vs target |
| 9 | Bottleneck + critical path автоматически |
| 10 | Unknown ≤ 5% wall time |
| 11 | Mono vs Dist — decode only |
| 12 | Regression diff после каждого run |
| 13 | HTML + JSON + CSV auto-saved |
| 14 | Zero overhead when `DIST_PERF_TRACE=0` |

---

## 16. Task 13 Readiness (gate)

**Task 13 запрещено начинать**, пока profiler не объясняет **≥ 95%** времени выполнения в каждой фазе:

| Phase | Explained buckets |
|-------|-------------------|
| Install | download + verify + reuse + store_write + planner + unknown |
| Session | configure + worker_startup + model_load + ready_wait + unknown |
| TTFT | prefill compute + network + idle + unknown |
| Decode | compute + network + serialize + idle + sampling + unknown |

Пример decode (допустимо):

```
Compute 22% | Network 3% | Serialization 2% | Idle 70% | Unknown 3%  ✓
```

Пример (недопустимо для Task 13):

```
Compute 40% | Unknown 55%  ✗
```

Task 13 = серия **точечных** оптимизаций по top bucket из `bottleneck.json`, с обязательным `regression_diff.json` после каждого изменения.

---

## 17. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Trace overhead skews TPS | Off by default; ring buffer; decode cap in CI |
| Clock skew across nodes | Durations + token order, not absolute cross-node ts |
| Metal GPU util API limited | Fallback: compute thread busy %; document confidence |
| Install trace volume (400+ blobs) | Aggregate per-stage + sample 10% blobs in CI |
| Regression false positives | WARN/FAIL thresholds; manual baseline pin |

---

## 18. Expected Report (reference)

```
Llama 3.2 1B — Performance Report
Budget: WARN 2/9 | FAIL 5/9

INSTALL (cold)
  Download .............. 712 s   (budget: reuse on 2nd run)
  Reuse on warm ......... 0%      (budget: 100%)  FAIL

SESSION CREATE (warm)
  Total ................. 19.3 s   (budget: ≤ 1 s)  FAIL
  ├─ Configure A/B/C .... 630 ms
  ├─ Worker startup ..... 1.8 s
  ├─ Model load ......... 2.7 s
  └─ READY wait ......... 0.8 s

TTFT
  Prefill ............... 4.5 s    (budget: ≤ 2 s warm)  FAIL

DECODE (vs mono 8.3 ms/tok)
  Entry compute ......... 7.9 ms
  Entry idle ............ 31.4 ms  (util 20%)
  Network / hop ......... 0.7 ms   (budget ≤ 1 ms)  PASS
  Middle idle ........... 29.8 ms   (queue depth p95: 2)
  Final compute ......... 7.8 ms
  GPU util (entry) ...... 18%      ← explains idle
  TOTAL ............... 115.2 ms/tok (+1287% vs mono)  FAIL

BOTTLENECK: Idle 78% | Compute 22% | Unknown 2%
CRITICAL PATH: Entry compute → Middle wait → Final wait → Sample

REGRESSION vs 2026-07-05:
  Install -80% ✓ | TTFT -60% ✓ | Decode +18% ✗
```
