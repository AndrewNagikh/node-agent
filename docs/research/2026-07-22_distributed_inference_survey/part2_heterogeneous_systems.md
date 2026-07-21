# Часть 2. Гетерогенные/распределённые системы: DeepSpeed Inference, Petals, Exo

> Методологическое примечание: часть источников получена через WebSearch (агрегированные выжимки), а не прямым чтением страницы — такие места отмечены явно. Для Exo особенно важно: проект имеет сложную историю (архивная Python-версия vs. текущая Rust-версия) — разобрано отдельно.

---

## 1. DeepSpeed Inference (Microsoft)

### 1.1 Архитектура

Две части:
1. **DeepSpeed Transformer** — GPU-only: (a) single-GPU кастомные kernel'ы под memory bandwidth при малых batch size; (b) многоgpu-масштабирование через **tensor-slicing** и **pipeline parallelism**; (c) sparse MoE в масштабе сотен GPU [Source: https://huggingface.co/papers/2207.00032].
2. **ZeRO-Inference** — GPU + CPU DRAM + NVMe для очень крупных моделей на малом числе GPU [Source: там же].

Tensor-slicing — внутри узла (максимизация memory bandwidth), pipeline — между узлами (меньше коммуникаций) [Source: там же]. Model parallelism: DeepSpeed «automatically partition[s] the model as necessary»; для несовместимых моделей — `injection_policy` (указывает два линейных слоя, между которыми нужен all-reduce) и `replace_with_kernel_inject` [Source: https://raw.githubusercontent.com/deepspeedai/DeepSpeed/master/docs/_tutorials/inference-tutorial.md].

Динамического scheduler'а между узлами нет — распределение задаётся конфигурацией до старта. **DeepSpeed-FastGen** («Inference v2») с **Dynamic SplitFuse**: длинные промпты бьются на чанки, короткие объединяются под «token budget»; заявлено до 2.3× throughput против vLLM [Source: https://www.marktechpost.com/2024/01/19/microsoft-ai-research-unveils-deepspeed-fastgen-elevating-llm-serving-efficiency-with-innovative-dynamic-splitfuse-technique/].

**KV cache**: стандартный, с офлоадом в CPU при превышении порогов, «architecture-aware scheduling» против конкуренции за PCIe [Source: https://huggingface.co/papers/2207.00032]; в ZeRO-Inference рост длины вывода в 5 раз требует снижения batch size ~в 2 раза [Source: https://www.deepspeed.ai/2022/09/09/zero-inference.html].

### 1.2 Аппаратные предположения

Датацентр: «heterogeneous network topology (e.g. intra-node NVLink/NVSwitch and inter-node InfiniBand)» [Source: https://huggingface.co/papers/2207.00032]. ZeRO-Inference — одиночный V100-32GB + 1.5TB CPU DRAM + 30TB NVMe для крупных моделей [Source: https://www.deepspeed.ai/2022/09/09/zero-inference.html]. Ethernet/интернет/домашняя сеть как целевая среда — не удалось подтвердить по открытым источникам.

### 1.3 Гетерогенность

fp32/fp16/int8 [Source: inference-tutorial.md]. ZeRO-Inference — «commodity»-конфигурации [Source: zero-inference blog]. Не-NVIDIA GPU, Apple Silicon, смешение производителей — не удалось подтвердить по открытым источникам.

### 1.4 Scheduler

Адаптивного динамического scheduler'а, пересчитывающего размещение слоёв во время работы — не обнаружено. ZeRO-Inference — «sequential layer streaming» с layer prefetching (ускорение 1.13–1.21x) [Source: https://www.deepspeed.ai/2022/09/09/zero-inference.html]. Dynamic SplitFuse — динамическое формирование батчей, не перераспределение слоёв [Source: marktechpost, см. выше].

### 1.5 Speculative decoding

**Не встроен**: открытый feature request в DeepSpeed-MII (issue #254) без подтверждённой реализации [Source: https://github.com/microsoft/DeepSpeed-MII/issues/254]. Академические работы строят SD *поверх* DeepSpeed-Inference («Decoding Speculative Decoding», arXiv:2402.01528), но это внешние исследования [Source: https://arxiv.org/pdf/2402.01528].

### 1.6 Pipeline

Меж-нодовый pipeline как дополнение к tensor-slicing внутри узла [Source: https://huggingface.co/papers/2207.00032]. Adaptive/heterogeneous pipeline — не удалось подтвердить; похоже на статичный, конфигурируемый заранее.

### 1.7 Сетевая модель

NVLink/NVSwitch + InfiniBand; PCIe для ZeRO-Inference. Компенсация задержек для WAN — не обнаружена, фреймворк не позиционируется для интернета/домашней сети.

### 1.8 Fault tolerance

Механизмов переподключения/смены узлов на лету не обнаружено — контролируемая датацентровая инфраструктура. Не удалось подтвердить наличие runtime fault tolerance, сравнимого с Petals.

---

## 2. Petals (BigScience / community)

### 2.1 Архитектура

Клиент-серверная архитектура с **pipeline parallelism**: клиент хранит только input/output embeddings (<3% весов BLOOM-176B), серверы — наборы **последовательных** трансформерных блоков: «This interval is always contiguous, since splitting it would harm the inference latency» [Source: https://arxiv.org/html/2312.08361]. Тензорный параллелизм не используется [Source: https://medium.com/@kannansarat9/part-1-inferencing-llama-2-70b-using-petals-swarm-model-parallelism-over-the-internet-a29de8f8aef3].

**KV cache — двойная схема**: server-side cache (attention keys/values для слоёв сервера) + client-side cache (входные активации, отправленные на каждую стадию) [Source: https://arxiv.org/html/2312.08361]. Это разделение — основа восстановления после отказа сервера без полного пересчёта (см. 2.8).

Активации между стадиями передаются напрямую сервер-сервер: «once the server obtains output activations, it sends them to both client and the subsequent stage» — оба сообщения по несколько килобайт, отправляются параллельно [Source: там же].

### 2.2 Аппаратные предположения

Потребительские GPU + обычный интернет: T4, A100, RTX 3090/3060/3070, 2080Ti, A4000, A5000 [Source: https://arxiv.org/html/2312.08361]. RTX 3070 выполняет полный шаг инференса BLOOM-176B менее чем за секунду [Source: там же]. Сетевые условия моделировались: 1 Gbit/s / 100 Mbit/s, <5 ms / 100 ms RTT; реальное развёртывание — лаборатории в Европе и Северной Америке на 100–1000 Mbit/s, часть за файрволами через Circuit Relay [Source: там же].

### 2.3 Гетерогенность

- 8-bit mixed matrix decomposition (~0.1% 16-bit outliers) — память вдвое [Source: https://arxiv.org/html/2312.08361]
- 4-bit NF4 (bitsandbytes): −40% GPU-памяти, ~2x ускорение против 8-bit [Source: https://github.com/bigscience-workshop/petals — WebSearch-агрегация]
- Активации сжимаются dynamic blockwise quantization — bandwidth вдвое [Source: https://arxiv.org/html/2312.08361]
- Гетерогенность эмулировалась «12 heterogeneous devices by partitioning each A100» [Source: там же]

Apple Silicon/AMD/Intel — не удалось подтвердить (ориентация на CUDA через bitsandbytes/PyTorch).

### 2.4 Scheduler — одна из сильнейших сторон

- Каждый сервер измеряет throughput сети (токены/сек через публичные API) и GPU (встроенный бенчмарк); минимум кэшируется как итоговый throughput [Source: https://arxiv.org/html/2312.08361].
- Выбор отрезка блоков минимизирует «бутылочное горлышко»: `start = argmin_i sorted([t_i, ..., t_{i+K-1}])`, лексикографическое сравнение отсортированных массивов throughput [Source: там же].
- Анонсы блоков и throughput — в **DHT** [Source: там же].
- **Ребалансировка**: узлы периодически проверяют, даст ли ребалансировка прирост ≥p% (p=20% в статье — баланс между эффективностью роя и издержками инвалидации кэша) [Source: там же].

**Прямой ответ на ключевой вопрос: да, Petals динамически пересчитывает размещение слоёв по узлам на основе измеренной производительности и латентности** — через периодическую самопроверку и DHT.

### 2.5 Speculative decoding

Не обнаружено ни в статье, ни в документации [Source: https://arxiv.org/html/2312.08361]. Не удалось подтвердить наличие.

### 2.6 Pipeline

«Полу-статичный»: стабилен между запросами, меняется при выгодной ребалансировке [Source: https://arxiv.org/html/2312.08361]. Fine-tuning — микробатчи по 1024 токена против pipeline bubbles. Каждый клиент прокладывает маршрут независимо, единого мульти-pipeline scheduler'а нет [Source: там же].

### 2.7 Сетевая модель — явно интернет-ориентированная

- Маршрутизация клиента — алгоритм **D\* Lite**, быстрая адаптация пути после ухода/бана сервера [Source: https://arxiv.org/html/2312.08361].
- Минимизируется суммарное время: throughput сервера + сетевая задержка (пинг при построении маршрута) [Source: там же].
- Эмпирика: **производительность слабо зависит от bandwidth, но деградирует с ростом латентности** — на каждом раунде передаются активации одного токена (несколько КБ) [Source: там же].

### 2.8 Fault tolerance — наиболее проработанный из всех трёх

- Отказ сервера обнаруживается через исключение (`catch ServerFailed` в псевдокоде), клиент заменяет сервер из своей кучи маршрутов [Source: https://arxiv.org/html/2312.08361].
- «If a remote server shuts down, any cached attention keys stored on that server will be lost with it» — но клиент восстанавливает состояние на новом сервере из **client-side cache** (`past_inputs = cache.pop(server)`) [Source: там же].
- Стоимость восстановления — O(t) на отказавший сервер; пересчитываются только стадии отказавшего [Source: там же].
- Гарантия: «run inference and fine-tuning over a swarm of unreliable devices with the same correctness guarantees as when running locally»; проверялось отключением случайных серверов [Source: там же].
- v2.0.0: «shortest-path routing, direct server-to-server communication» [Source: https://github.com/bigscience-workshop/petals/releases/tag/v2.0.0.post1].
- Мониторинг роя — health.petals.dev [Source: https://github.com/petals-infra/health.petals.dev].

---

## 3. Exo (exo-explore/exo)

### 3.1 Историческая ремарка (важно)

Изначальная Python-версия (tinygrad + MLX, «ring memory weighted partitioning») архивирована как **exo-explore/ex-exo** [Source: https://github.com/exo-explore/ex-exo]. Активный **exo-explore/exo** — переписан: ядро на **Rust**, **zenoh** pub/sub, MLX как единственный inference backend, master/worker вместо чистого p2p [Source: https://github.com/exo-explore/exo].

### 3.2 Архитектура

**Архивная версия**: чистый pipeline, p2p без master-worker: «exo devices connect p2p» [Source: https://github.com/exo-explore/ex-exo].

**Текущая версия**: pipeline **и** tensor parallelism (TP для `supports_tensor = true`; заявлено до 1.8x на 2 устройствах, 3.2x на 4); pipeline оборачивает границы диапазонов слоёв send/recv-слоями [Source: WebSearch-агрегация README exo-explore/exo]. Заявлен **«Topology-Aware Auto Parallel»** — оптимальное распределение по ресурсам устройств и латентности/bandwidth сети [Source: https://github.com/exo-explore/exo].

Событийно-ориентированная архитектура: Master упорядочивает изменения состояния в `DiskEventLog`, Workers применяют журнал (`State`, функция `apply`); Master обрабатывает команды (`PlaceInstance`) через **Placement Engine** [Source: https://deepwiki.com/exo-explore/exo — вторичный автогенерируемый источник, требует верификации по коду].

**Partitioning (архивная версия)**: единственная стратегия — `RingMemoryWeightedPartitioningStrategy` (`exo/topology/ring_memory_weighted_partitioning_strategy.py`): партиция слоёв пропорциональна памяти устройства [Source: https://github.com/exo-explore/exo/issues/284]. В открытом issue **[BOUNTY - $500] Better PartitioningStrategy (#284)** мейнтейнеры признают: стратегия учитывает **только память**, игнорирует FLOPS, латентность и bandwidth, статична; отмечен конфликт целей TTFT vs tokens-per-second [Source: там же].

### 3.3 Аппаратные предположения

Домашняя/потребительская сеть, от MacBook до Raspberry Pi [Source: https://www.cnx-software.com/2025/02/18/exo-software-a-distributed-llm-solution-running-on-a-cluster-of-computers-smartphones-or-sbcs/]. Текущая версия: **RDMA over Thunderbolt 5** («99% reduction in latency», требует macOS 26.2+, M4 Pro/Max, M3 Ultra) [Source: https://github.com/exo-explore/exo]. Обнаружение устройств — UDP (в архивной версии UDP/manual/Tailscale, gRPC peer-networking) [Source: https://github.com/exo-explore/ex-exo].

### 3.4 Гетерогенность

**Архивная версия**: MLX (Apple Silicon), CUDA (NVIDIA/Linux), tinygrad (кроссплатформенно), Raspberry Pi [Source: https://github.com/exo-explore/ex-exo]. **Текущая версия** заметно уже: GPU только на macOS/Apple Silicon (MLX); Linux — CPU-only, GPU «under development»; ROCm/AMD — открытый issue #434, статус не удалось подтвердить (содержимое обсуждения не получено из-за лимита инструмента — считать неподтверждённым, а не отсутствующим).

### 3.5 Scheduler

Текущая версия: «the cluster re-evaluates optimal partitioning based on current conditions... every few seconds»; при недоступности устройства «automatically repartitions the model across remaining devices within seconds» [Source: WebSearch-агрегация README/docs exo-explore/exo]. При этом issue #284 фиксирует, что дефолтная встроенная стратегия адаптивной по FLOPS/латентности не является — **заявления README и фактическая логика могут не совпадать; трактовать с осторожностью без прямого чтения кода `exo/topology/`** [Source: https://github.com/exo-explore/exo/issues/284].

### 3.6 Speculative decoding

Не найдено упоминаний ни в документации, ни в issues — не удалось подтвердить по открытым источникам.

### 3.7 Сетевая модель

Акцент на **локальную/домашнюю сеть** (RDMA over Thunderbolt 5), не WAN [Source: https://github.com/exo-explore/exo]. Issue #1726: «2-node Mac cluster... becomes unstable after a few prompts: peer queues full → broken pipe → worker teardown» [Source: https://github.com/exo-explore/exo/issues/1726 — через WebSearch]. WAN-развёртывание — подтверждений не найдено.

### 3.8 Fault tolerance

Заявлено автоперераспределение при потере устройства «within seconds» [Source: WebSearch-агрегация README]. Открытые issues показывают проблемы согласованности: #1732 «Deleting an instance leaves stale runner state in master state» [Source: https://github.com/exo-explore/exo/issues/1732]; #1726 — каскадный отказ под нагрузкой. В отличие от формализованного алгоритма Petals (dual attention cache с гарантиями корректности), у Exo fault tolerance — заявление уровня README, открытые issues показывают активную незавершённую работу.

---

## Сводная таблица

| Критерий | DeepSpeed Inference | Petals | Exo |
|---|---|---|---|
| Layer Pipeline | ✅ | ✅ | ✅ |
| Tensor Parallel | ✅ | ❌ (только pipeline) | ✅ (текущая версия) |
| Dynamic Scheduler | ❌ | ✅ (DHT-throughput ребалансировка) | ⚠️ (заявлено, но дефолтная стратегия статична — issue #284) |
| Adaptive Partition | ❌ | ✅ (min-bottleneck по измеренному throughput) | ⚠️ |
| Heterogeneous Nodes | ⚠️ | ✅ (разные GPU, 8-bit/NF4) | ⚠️ (только Apple Silicon GPU + Linux CPU) |
| Ethernet-first | ⚠️ | ✅ (явно интернет) | ✅ (домашняя сеть/Thunderbolt) |
| WAN Support | ❌ | ✅ (межконтинентально, Circuit Relay) | ❌/не подтверждено |
| Tree Speculative | ❌/? | ❌/? | ❌/? |
| Multiple Draft Models | ❌/? | ❌/? | ❌/? |
| Verify Tree | ? | ? | ? |
| Distributed Verification | ? | ? | ? |
| Distributed KV Cache | ⚠️ (offload в CPU) | ✅ (server/client split KV) | ⚠️/? |
| Dynamic Layer Migration | ❌ | ✅ (ребалансировка при выигрыше >p%) | ✅ (заявлено, «within seconds») |

**Легенда**: ✅ реализовано, ⚠️ частично/с оговорками, ❌ отсутствует, ? невозможно определить по открытым источникам.

---

## Ограничения этого исследования

1. Часть фактов о текущей версии Exo получена через WebSearch-выжимки (WebFetch упёрся в лимит) — рекомендуется верификация прямым чтением `exo/topology/`, `exo/master/`, `exo/worker/`.
2. Для DeepSpeed статья arXiv:2207.00032 не распарсилась как PDF — использованы huggingface.co/papers + deepspeed.ai + tutorial.
3. Tree Speculative / Multiple Draft Models / Verify Tree / Distributed Verification не обнаружены ни в одном из трёх фреймворков по открытым источникам.
