# Supported Architectures (v0.1)

Реестр поддерживаемых семейств моделей в distributed runtime.  
Проверяется через `./scripts/run_architecture_suite.sh` (локально) и `llama.cpp/tools/distributed/docker/run_e2e_generate.py` (кластер).

## Статусы

| Значение | Значение |
|----------|----------|
| ✅ | Полностью поддержано и проверено в E2E |
| 🟡 | Частично: sync/install/coverage OK, distributed generate не прогонялся или через родительское семейство |
| ❌ | Не поддержано |

## Реестр

| Architecture | Статус | Partial Forward | Hidden Injection | Layer-first | Generate | Verification |
|--------------|--------|-----------------|------------------|-------------|----------|--------------|
| Llama | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Qwen | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Gemma | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Phi | 🟡 | ✅ | ✅ | ✅ | Sync | ✅ |
| SmolLM | 🟡 | ✅ | ✅ | ✅ | Sync | ✅ |
| DeepSeek-Qwen | 🟡 | Через Qwen | Через Qwen | ✅ | Sync | ✅ |

### Модели в матрице E2E

| Family | Model ID | GGUF | Full generate |
|--------|----------|------|---------------|
| llama | `tinyllama-1.1b` | TinyLlama 1.1B Chat Q4_K_M | ✅ |
| llama | `llama-3.2-1b` | Llama 3.2 1B Instruct Q4_K_M | ✅ |
| qwen | `qwen2.5-1.5b` | Qwen2.5 1.5B Instruct Q4_K_M | ✅ |
| gemma | `gemma-3-1b` | Gemma 3 1B IT Q4_K_M | ✅ |
| phi | `phi-3.5-mini` | Phi-3.5 Mini Instruct Q4_K_M | Sync only |
| smollm | `smollm2-1.7b` | SmolLM2 1.7B Instruct Q4_K_M | Sync only |
| deepseek | `deepseek-r1-distill-qwen-1.5b` | DeepSeek-R1-Distill-Qwen 1.5B Q4_K_M | Sync only |

Конфигурация: `config/architecture_matrix.json`.

### Примечания

- **Layer-first** — install planner и materializer работают через semantic blobs и layer indices, без byte-range хаков.
- **Partial Forward** — `layer_start` / `layer_end` + hidden injection в graph (`llama.cpp`, `qwen2.cpp`, `gemma3.cpp`; Phi/SmolLM — descriptor + plugin).
- **DeepSeek-Qwen** — производная архитектура; runtime descriptor и plugin наследуют Qwen.
- **Sync** — register → discover → manifest → layout → install → coverage READY; без `session/generate`.

## Добавление новой архитектуры

1. Реализовать `ArchitecturePlugin` в `llama.cpp/tools/distributed/architecture/plugins/`.
2. Зарегистрировать в `descriptor_service.cpp`.
3. При необходимости — partial forward в `llama.cpp/src/models/<arch>.cpp`.
4. Добавить запись в `config/architecture_matrix.json` и эту таблицу.
5. Прогнать verification suite.

Подробнее: [llama.cpp/docs/task9_9_runtime_stabilization.md](../llama.cpp/docs/task9_9_runtime_stabilization.md).
