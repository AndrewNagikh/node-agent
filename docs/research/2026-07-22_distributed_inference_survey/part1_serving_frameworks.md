# Часть 1. Serving-фреймворки: vLLM, SGLang, TensorRT-LLM

Детальный разбор трёх фреймворков по восьми измерениям, с опорой на официальную документацию, исходный код репозиториев на GitHub и опубликованные статьи/блог-посты. Там, где публичные источники не дают однозначного ответа, это указано явно.

---

## 1. vLLM

### 1.1 Архитектура

vLLM построен вокруг движка непрерывного батчинга (continuous batching) поверх PagedAttention. Основная статья — «Efficient Memory Management for Large Language Model Serving with PagedAttention» (Kwon et al., SOSP'23) — описывает разбиение KV-кэша на страницы фиксированного размера (`BLOCK_SIZE`), которые могут не быть смежными физически, по аналогии с виртуальной памятью ОС [Source: https://en.wikipedia.org/wiki/PagedAttention]. Согласно официальной документации, каждый блок хранит фиксированное число токенов для каждой head; ключевой и value-кэш имеют раздельные layout'ы `[num_blocks, num_kv_heads, head_size/x, block_size, x]` и `[num_blocks, num_kv_heads, head_size, block_size]` [Source: https://docs.vllm.ai/en/latest/design/paged_attention/].

Планирование запросов реализовано в vLLM V1 в директории `vllm/v1/core` — управление KV-кэшем разнесено по файлам `kv_cache_manager.py`, `kv_cache_coordinator.py`, `single_type_kv_cache_manager.py`, `block_pool.py`, а сам scheduler находится в `vllm/v1/core/sched/scheduler.py` [Source: https://github.com/vllm-project/vllm/tree/main/vllm/v1/core]. Класс `Scheduler` реализует интерфейс `SchedulerInterface` и работает по «унифицированной» модели без явного разделения на «prefill» и «decode»-фазы: у каждого запроса отслеживаются `num_computed_tokens` и `num_tokens_with_spec`, и на каждом шаге планировщик пытается выделить токен-бюджет запросам — этот же механизм используется для chunked prefill, prefix caching и speculative decoding одновременно [Source: https://github.com/vllm-project/vllm/blob/main/vllm/v1/core/sched/scheduler.py]. Порядок обработки: сначала уже выполняющиеся (`running`) запросы, затем — запросы из очереди ожидания (`waiting`), с ограничениями `max_num_batched_tokens` и `max_num_seqs`; при нехватке KV-блоков применяется вытеснение (`_preempt_request()`) по приоритету и времени поступления [Source: https://github.com/vllm-project/vllm/blob/main/vllm/v1/core/sched/scheduler.py].

Распределение слоёв/тензоров: vLLM поддерживает тензорный параллелизм (реализация алгоритма Megatron-LM: column-parallel + row-parallel слои) и pipeline-параллелизм (последовательные блоки слоёв на разных узлах), а также expert-parallelism и data-parallelism для MoE-моделей [Source: https://docs.vllm.ai/en/stable/serving/parallelism_scaling/] [Source: https://github.com/vllm-project/vllm/blob/main/vllm/distributed/parallel_state.py]. Синхронизация между воркерами — NCCL-коллективы, оркестрируемые Ray (multi-node) либо через нативный Python `multiprocessing` (single-node) [Source: https://docs.vllm.ai/en/stable/serving/parallelism_scaling/].

Отдельный механизм — disaggregated prefilling: prefill и decode выполняются в разных инстансах vLLM, связанных абстракциями `Connector` (передача KV-кэша), `LookupBuffer` (`insert`/`drop_select`) и `Pipe` (однонаправленный FIFO-канал); весь код лежит в `vllm/distributed/kv_transfer` [Source: https://docs.vllm.ai/en/latest/features/disagg_prefill/] [Source: https://github.com/vllm-project/vllm/issues/10818]. Поддерживаются коннекторы NixlConnector, MooncakeConnector, LMCacheConnectorV1, OffloadingConnector, FlexKVConnectorV1 и др. [Source: https://docs.vllm.ai/en/latest/features/disagg_prefill/].

### 1.2 Аппаратные предположения

Базовый сценарий — один или несколько NVIDIA GPU в одном датацентровом узле с NVLink; для меж-узлового тензорного параллелизма документация прямо рекомендует «высокоскоростные сетевые адаптеры, такие как InfiniBand», а pipeline-параллелизм рекомендован именно тогда, когда между GPU нет NVLink [Source: https://docs.vllm.ai/en/stable/serving/parallelism_scaling/]. Поддерживается GPUDirect RDMA [Source: там же]. WAN/интернет-сценарии в официальной документации не описаны — не удалось подтвердить по открытым источникам.

### 1.3 Гетерогенность

vLLM официально поддерживает NVIDIA GPU, AMD GPU (ROCm ≥6.3), Intel GPU (XPU backend), Google TPU, x86/ARM/PowerPC CPU [Source: https://docs.vllm.ai/en/latest/getting_started/installation/gpu/] [Source: https://github.com/vllm-project/vllm]. Через «Hardware-Pluggable RFC» существуют сторонние плагины для Intel Gaudi, IBM Spyre, Huawei Ascend, Rebellions NPU, MetaX GPU и Apple Silicon (community-плагин `vLLM-Metal` на MLX/Metal) [Source: https://github.com/vllm-project/vllm]. При этом одновременное смешение разных вендоров GPU в рамках одного распределённого инференс-кластера в официальной документации не описано — поддерживаются разные бэкенды по отдельности, а не гибридные развёртывания; не удалось подтвердить обратное по открытым источникам.

### 1.4 Scheduler

Внутри одного инстанса — токен-бюджетный итеративный планировщик (см. 1.1): continuous batching, chunked prefill, prefix caching [Source: https://github.com/vllm-project/vllm/blob/main/vllm/v1/core/sched/scheduler.py]. На уровне кластера — **vLLM Router** (Rust): state-aware балансировщик, учитывающий состояние KV-кэша и prefill/decode-топологию [Source: https://vllm.ai/blog/2025-12-13-vllm-router-release]. Проект **llm-d** добавляет prefix-cache-aware маршрутизацию и явно поддерживает гетерогенные конфигурации по ролям (мощные GPU — под prefill, дешёвые — под decode) [Source: https://llm-d.ai/blog/production-grade-llm-inference-at-scale-kserve-llm-d-vllm]. Динамическое перераспределение слоёв модели («layer repartitioning» на лету) в открытых источниках не описано — не удалось подтвердить.

### 1.5 Speculative decoding

Поддерживаются множественные схемы: EAGLE, EAGLE-3, Medusa, обычная draft-model схема, ngram-proposer, dflash, suffix-decoding — реализации в `vllm/v1/spec_decode/` (`eagle.py`, `medusa.py`, `ngram_proposer.py`, `dflash.py`, `draft_model.py`) [Source: https://github.com/vllm-project/vllm]. Верификация — через `Rejection Sampler` (lossless-гарантия); оптимизации `Block Verify` и `Entropy Verify` [Source: https://docs.vllm.ai/en/latest/features/speculative_decoding/]. Древовидная верификация — через EAGLE/Medusa-style tree attention [Source: https://github.com/vllm-project/vllm/issues/4630]. Явное ограничение: при TP основной модели draft-модель в ряде версий должна работать без TP [Source: https://docs.vllm.ai/en/v0.9.0/features/spec_decode.html].

### 1.6 Pipeline

Pipeline parallelism обозначен как «beta»: статическое разбиение слоёв (`pipeline_parallel_size`), сочетается с TP внутри узла [Source: https://docs.vllm.ai/en/stable/serving/parallelism_scaling/]. Адаптивное/динамическое перераспределение границ стадий — не найдено, не удалось подтвердить по открытым источникам.

### 1.7 Сетевая модель

Датацентровая сеть с быстрым интерконнектом: NVLink внутри узла, InfiniBand/RDMA между узлами [Source: https://docs.vllm.ai/en/stable/serving/parallelism_scaling/]. Алгоритмов компенсации задержек для Ethernet/WAN — не описано, не удалось подтвердить.

### 1.8 Fault tolerance

Встроенная отказоустойчивость ядра ограничена. Разрыв NIXL-соединений при рестарте требует полного передеплоя обеих сторон [Source: https://github.com/vllm-project/vllm/issues/38840]. RFC «Fault-Tolerant Expert Parallelism» [Source: https://github.com/vllm-project/vllm/issues/27774] и RFC для vLLM-Ascend [Source: https://github.com/vllm-project/vllm-ascend/issues/5067] — статус: предложения. На уровне оркестрации Ray Serve LLM — отказоустойчивость на уровне DP-групп [Source: https://www.anyscale.com/blog/dp-group-fault-tolerance-vllm-wideep-ray-serve-llm]. Итого: fault tolerance — в основном на уровне внешней оркестрации, не ядра.

---

## 2. SGLang

### 2.1 Архитектура

SGLang («SGLang: Efficient Execution of Structured Language Model Programs», arXiv:2312.07104) — встраиваемый Python front-end язык + оптимизированный runtime [Source: https://arxiv.org/abs/2312.07104]. Центральный механизм KV-кэша — **RadixAttention**: KV-кэш в виде radix-дерева, листья вытесняются по LRU; кэш-тензоры в постраничной GPU-памяти (page size = 1 токен), структура дерева на CPU [Source: https://www.lmsys.org/blog/2024-01-17-sglang/]. Cache-hit rate 50%–99% [Source: там же].

Планировщик — «overlapped scheduling»: пока GPU обрабатывает текущий батч, CPU готовит следующий (zero-overhead batch scheduler в v0.4) [Source: https://www.lmsys.org/blog/2024-12-04-sglang-v0-4/]. Cache-aware scheduling группирует запросы с общими префиксами [Source: https://www.lmsys.org/blog/2024-01-17-sglang/].

Распределение: TP, PP, EP (MoE), DP + отдельный режим Disaggregated Inference (prefill/decode на разных группах узлов); миксины `SchedulerDisaggregationPrefillMixin` / `SchedulerDisaggregationDecodeMixin`, публикация состояния кэша через `SchedulerKvEventsPublisher` [Source: https://deepwiki.com/sgl-project/sglang/2.4-disaggregation-architecture — вторичный источник, автогенерируемая обёртка над кодом]. Минимизация коммуникаций: Chunked Pipeline Parallelism, кастомные all-reduce под NVLink, MSCCLPP, symmetric memory на Blackwell/Hopper [Source: там же].

### 2.2 Аппаратные предположения

Датацентровый GPU-кластер: NVLink внутри узла, InfiniBand между узлами для disaggregation KV-cache transfer, TCP/ZMQ для control-plane [Source: https://deepwiki.com/sgl-project/sglang/2.4-disaggregation-architecture]. Multi-Node NVLink и symmetric memory — Blackwell/Hopper [Source: там же].

### 2.3 Гетерогенность

CUDA (NVIDIA), HIP/ROCm (AMD MI300X/MI325X/MI350X с AITER), Intel XPU/Xeon (AMX), Ascend NPU, MUSA (Moore Threads), CPU [Source: https://github.com/sgl-project/sglang/blob/main/docs/platforms/amd_gpu.md]. Смешение вендоров в одном TP/PP кластере — свидетельств не найдено.

### 2.4 Scheduler

**SGLang Router** — HTTP-прокси с политиками random, round-robin, cache-aware, power-of-two [Source: https://docs.vast.ai/examples/serving-infrastructure/sglang-router-vast]. Cache-aware балансировщик (v0.4) поддерживает приближённую копию radix-дерева воркеров; прирост до 1.9× throughput и 3.8× cache-hit rate [Source: https://www.lmsys.org/blog/2024-12-04-sglang-v0-4/]. Dynamic Chunking (см. 2.6) — форма adaptive/latency-aware перепланирования [Source: https://www.lmsys.org/blog/2026-01-15-chunked-pipeline/].

### 2.5 Speculative decoding

Алгоритмы через `--speculative-algorithm`: EAGLE-2, EAGLE-3, MTP, DFLASH, STANDALONE (классическая draft-model схема), NGRAM (дерево кандидатов из кэша предыдущих токенов через BFS) [Source: https://docs.sglang.io/advanced_features/speculative_decoding.html]. EAGLE-2: дерево на `--speculative-num-steps` шагов с ветвлением `--speculative-eagle-topk`, ранжирование, верификация. EAGLE-3 на LLaMA-3.1-8B: ≈373 ток/с против 158 baseline и 244 у EAGLE-2 [Source: там же]. FR-Spec — усечённый по частоте словарь LM-head драфта. Overlap-scheduler для асинхронного draft/verify (требует `--speculative-eagle-topk 1`); DFLASH требует `pp_size==1`; NGRAM — только CUDA [Source: там же]. Для обучения EAGLE3-драфтов — проект **SpecForge** [Source: https://www.lmsys.org/blog/2025-07-25-spec-forge/].

### 2.6 Pipeline

Документирован детально, включая «Chunked Pipeline Parallelism» (блог LMSYS 15.01.2026) [Source: https://www.lmsys.org/blog/2026-01-15-chunked-pipeline/]. Разбиение слоёв — статическое, но конфигурируемое `SGLANG_PP_LAYER_PARTITION`. Длинный промпт бьётся на чанки 4K–12K токенов с перекрытием по стадиям. Асинхронный event loop с неблокирующим P2P (`async_send` в `_pp_send_pyobj_to_next_stage`, `P2PWork`, `_pp_commit_comm_work`), раздельные CUDA-стримы [Source: https://docs.sglang.io/advanced_features/pipeline_parallelism.html]. **Dynamic Chunking** — предсказание оптимального размера следующего чанка через квадратичную модель времени выполнения (`SGLANG_DYNAMIC_CHUNKING_SMOOTH_FACTOR`, default 0.75); bubble-коэффициент `(P-1)/(P-1+M)` [Source: https://www.lmsys.org/blog/2026-01-15-chunked-pipeline/]. Результаты: TTFT −67.9% (PP4×TP8 vs PP1×TP8), throughput prefill ×3.31 на DeepSeek-V3.1 [Source: там же].

### 2.7 Сетевая модель

NVLink, InfiniBand/RoCE для коллективов, TCP/ZMQ для control-plane [Source: https://deepwiki.com/sgl-project/sglang/2.4-disaggregation-architecture]. Компенсация задержек для Ethernet-only/WAN — не найдено, не удалось подтвердить.

### 2.8 Fault tolerance

**Elastic EP** (для MoE): снимает жёсткую привязку эксперта к GPU, избыточные копии экспертов; при отказе — перераспределение весов и перенаправление токенов без остановки инференса; флаги `--elastic-ep-backend mooncake`, `--ep-num-redundant-experts` [Source: https://www.lmsys.org/blog/2026-03-25-eep-partial-failure-tolerance/]. Открытый RFC «Internal Process-level Fault Tolerance» — статус: обсуждение [Source: https://github.com/sgl-project/sglang/issues/22344]. Динамическое добавление воркеров — на уровне SGLang Router; детальный протокол не описан полностью — не удалось подтвердить.

---

## 3. TensorRT-LLM

### 3.1 Архитектура

C++/Python библиотека NVIDIA, компилирует модели в TensorRT-движки; асинхронный `Executor API` (`cpp/include/tensorrt_llm/executor/executor.h`, `enqueueRequest`/`enqueueRequests`) [Source: https://nvidia.github.io/TensorRT-LLM/advanced/executor.html] [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/cpp/include/tensorrt_llm/executor/executor.h]. Ключевой механизм — **in-flight batching**: на каждой decode-итерации формируются микробатчи из всех активных последовательностей, новые запросы вставляются без ожидания завершения батча [Source: https://github.com/NVIDIA/TensorRT-LLM/issues/2027]. Три политики: `GUARANTEED_NO_EVICT` (default), `MAX_UTILIZATION`, `STATIC_BATCH` [Source: https://nvidia.github.io/TensorRT-LLM/performance/performance-tuning-guide/useful-runtime-flags.html].

KV-кэш: `KVCacheManager` (наследует `BaseResourceManager`), пул страничных блоков, буфер `[num_blocks, 2, num_tokens_per_block, num_kv_heads, head_dim]` [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/torch/kv_cache_manager.md]. KV-cache reuse между запросами с общими префиксами [Source: https://nvidia.github.io/TensorRT-LLM/advanced/kv-cache-reuse.html].

Параллелизм: TP, PP, DP, EP (TP-only / EP-only / гибридный ETP), CP, Wide-EP (репликация «горячих» экспертов, offline/online балансировка) [Source: https://nvidia.github.io/TensorRT-LLM/features/parallel-strategy.html]. Коммуникация — NCCL-плагин (`cpp/tensorrt_llm/plugins/ncclPlugin`); Wide-EP оптимизирован под GB200 MNNVL [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/features/parallel-strategy.md].

### 3.2 Аппаратные предположения

Исключительно NVIDIA, от одного GPU до multi-node: NCCL + GPUDirect RDMA [Source: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tutorials/Deployment/Kubernetes/TensorRT-LLM_Multi-Node_Distributed_Models/README.html]. Жёсткая привязка к CUDA-стеку (CUDA 12.4+, TensorRT 10.x, NCCL 2.21+) [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/reference/support-matrix.md].

### 3.3 Гетерогенность

Только NVIDIA; AMD/Intel/Apple не поддерживаются [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/reference/support-matrix.md]. Disaggregated serving требует «однородного KV-кэша (одинаковый dtype, число attention-голов)» между context- и generation-серверами [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/features/disagg-serving.md]. Академические работы (Hetis, Cronus, Tangram) отмечают отсутствие нативной поддержки гетерогенных GPU-кластеров в production-движках, включая TensorRT-LLM [Source: https://arxiv.org/pdf/2509.08309] [Source: https://arxiv.org/pdf/2509.17357].

### 3.4 Scheduler

На уровне Executor — три политики in-flight batching. На уровне кластера — внешний оркестратор **NVIDIA Dynamo**: «smart router» определяет decode-worker по доступности KV-cache-блоков; топология «coordinator + worker fleet», воркеры stateless через `SO_REUSEPORT` [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/features/disagg-serving.md]. Dynamo поддерживает runtime-реконфигурируемую топологию xPyD с добавлением/удалением воркеров на лету [Source: docs.nvidia.com/dynamo]. Динамический layer repartitioning — не найден, не удалось подтвердить.

### 3.5 Speculative decoding

Поддерживаются: классическая draft/target-схема, Medusa (деревья через `Medusa choices`), EAGLE-1/2/3 (EAGLE-3 с tree-structured drafting, всё внутри TensorRT-движка), NGram/Prompt Lookup, MTP (с «relaxed acceptance» для reasoning-моделей), PARD, DFlash, Lookahead decoding (Jacobi-style) [Source: https://nvidia.github.io/TensorRT-LLM/advanced/speculative-decoding.html] [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/features/speculative-decoding.md]. Ограничение: динамический tree mode не поддерживается для sliding window attention / MLA (DeepSeek, gpt-oss) [Source: там же].

### 3.6 Pipeline

Статическое разбиение слоёв (PP — один из 6 режимов) [Source: https://nvidia.github.io/TensorRT-LLM/features/parallel-strategy.html]. Adaptive/dynamic перепланирование границ стадий — не найдено, не удалось подтвердить; разбиение фиксируется на этапе построения движка.

### 3.7 Сетевая модель

Датацентровый интерконнект: NCCL + GPUDirect RDMA, InfiniBand/RoCE через UCX, NVLink. Основной транспорт disaggregated-serving — **NIXL** (UCX default, LIBFABRIC с v0.16.0) [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/features/disagg-serving.md]. NIXL поддерживает RDMA/InfiniBand, RoCE, TCP fallback, NVMe-oF, S3; суб-5мс латентность передачи на InfiniBand HDR, от 50мс — признак TCP fallback [Source: ai-infrastructure.net/kv-cache-transfer-nixl]. WAN — не описано, не удалось подтвердить.

### 3.8 Fault tolerance

Ограничена: «fleet worker fails fast если coordinator недоступен» [Source: https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/features/disagg-serving.md]. Ограничения disaggregated serving: только decoder-only, `beam width = 1`, однородный KV-кэш [Source: там же]. Динамическая отказоустойчивость — во внешнем NVIDIA Dynamo (discovery-сервис, `RuntimeConfig`, дренаж при удалении) [Source: docs.nvidia.com/dynamo].

---

## Сводная таблица (vLLM, SGLang, TensorRT-LLM)

| Критерий | vLLM | SGLang | TensorRT-LLM |
|---|---|---|---|
| Layer Pipeline (PP) | ⚠️ beta, статическое | ✅ статич. + Dynamic Chunking, оверлап | ✅ статическое |
| Tensor Parallel (TP) | ✅ | ✅ | ✅ |
| Dynamic Scheduler | ✅ (continuous batching) | ✅ (overlap + cache-aware router) | ✅ (in-flight batching) |
| Adaptive Partition | ? | ✅ (Dynamic Chunking) | ❌ |
| Heterogeneous Nodes (вендоры в одном кластере) | ⚠️ (бэкенды по отдельности) | ⚠️ (бэкенды по отдельности) | ❌ (только NVIDIA) |
| Ethernet-first | ❌ | ❌ | ❌ |
| WAN Support | ? | ? | ? |
| Tree Speculative | ✅ | ✅ | ✅ |
| Multiple Draft Models | ⚠️ | ✅ (переключаемые алгоритмы) | ✅ (множественные схемы) |
| Verify Tree | ✅ | ✅ | ✅ |
| Distributed Verification | ⚠️ (draft без TP) | ? | ? |
| Distributed KV Cache | ✅ (kv_transfer) | ✅ (mooncake/nixl) | ✅ (NIXL/UCX) |

**Легенда**: ✅ реализовано; ⚠️ частично/с оговорками; ❌ отсутствует; ? не удалось определить по открытым источникам. Обоснования каждой ячейки — в тексте выше.

---

### Замечание по методологии

Часть материала о SGLang (имена внутренних классов disaggregation-миксинов) получена через DeepWiki — автогенерируемую обёртку над исходным кодом, вторичный источник. Остальные утверждения — официальная документация (docs.vllm.ai, nvidia.github.io/TensorRT-LLM, docs.sglang.io), блоги проектов (vllm.ai/blog, lmsys.org/blog) и файлы в GitHub-репозиториях.
