# Обзор: архитектурные механизмы распределённого инференса LLM

*(раздел технического обзора; методология: только открытые источники — arXiv, официальные репозитории, блоги; каждое утверждение — со ссылкой)*

---

## 1. Pipeline Parallelism (конвейерный параллелизм)

**1. Что это такое.** Модель разбивается по глубине: последовательные группы слоёв (стадии) размещаются на разных устройствах, а через них последовательно прогоняются микро-батчи запросов/токенов, чтобы устройства не простаивали, ожидая друг друга. Проблема, которую решает механизм — модель не помещается в память одного устройства, и без микро-батчинга пайплайн простаивал бы (bubble) [Source: https://arxiv.org/pdf/1811.06965].

**2. Ключевые реализации.**
- **GPipe** (Google) — ввёл разбиение мини-батча на микро-батчи и schedule fill-drain: "When the number of micro-batches M is at least 4× the number of partitions, the bubble overhead is almost negligible" [Source: https://arxiv.org/pdf/1811.06965].
- **PipeDream** (Microsoft/CMU, "PipeDream: Fast and Efficient Pipeline Parallel DNN Training") — асинхронный 1F1B (one-forward-one-backward) schedule, снижает коммуникацию "up to 95%" по сравнению с data-parallel, ценой staleness весов [Source: https://arxiv.org/abs/1806.03377].
- **Megatron-LM** ("Efficient Large-Scale Language Model Training on GPU Clusters Using Megatron-LM") — интерливинг (interleaved 1F1B): доля простоя = (1/v)·(p−1)/m, где v — число чанков модели на устройство, p — степень pipeline-параллелизма, m — число микро-батчей; уменьшает bubble в v раз ценой дополнительной коммуникации [Source: https://arxiv.org/pdf/2104.04473].

**3. Требования к сети.** По границе стадий передаются только активации/градиенты одного среза (обычно [batch, seq, hidden] тензор) — на порядки меньше, чем all-reduce тензорного параллелизма, и коммуникация может почти полностью перекрываться вычислением при достаточном числе микро-батчей [Source: https://arxiv.org/pdf/1806.03377]. Именно поэтому в гибридных схемах (Megatron-LM) tensor-parallel держат внутри узла (NVLink), а pipeline-parallel — между узлами, где связь заведомо медленнее [Source: https://arxiv.org/pdf/2104.04473].

**4. Гетерогенность.** Классические GPipe/PipeDream/Megatron-LM рассчитаны на **однородный** кластер — стадии нарезаются по числу слоёв, балансировка компромиссная, но не адаптируется к разной скорости узлов. Явно ориентированные на гетерогенность системы описаны ниже в разделе 5 (Petals/SWARM, Hetis, HexGen, LLM-PQ, Helix).

**5. Ограничения.** Bubble остаётся ненулевым при недостатке микро-батчей; PipeDream жертвует свежестью весов (staleness) ради устранения простоя [Source: https://arxiv.org/abs/1806.03377]; интерливинг в Megatron-LM снижает bubble, но "at the cost of extra communication" [Source: https://arxiv.org/pdf/2104.04473].

---

## 2. Tensor Parallelism (тензорный параллелизм)

**1. Что это такое.** Отдельные матрицы весов (например, MLP и attention-проекции) разрезаются между устройствами, каждое считает свою часть матричного умножения, а результаты синхронизируются all-reduce'ом внутри каждого слоя. Решает проблему, когда даже один слой не помещается в память/не даёт нужной задержки на одном устройстве [Source: https://arxiv.org/abs/1909.08053].

**2. Ключевые реализации.** Основополагающая работа — **Megatron-LM**: "Megatron-LM: Training Multi-Billion Parameter Language Models Using Model Parallelism" (Shoeybi et al., 2019) [Source: https://arxiv.org/abs/1909.08053], развитая в "Efficient Large-Scale Language Model Training on GPU Clusters Using Megatron-LM" [Source: https://arxiv.org/pdf/2104.04473]. Ключевая деталь дизайна: "each tensor parallel group is placed within a server node such that its communications can utilize high-bandwidth intra-server interconnects (i.e., NVLink)" [Source: https://arxiv.org/pdf/2104.04473].

**3. Требования к сети.** Это самый чувствительный к сети механизм из всех: синхронизация происходит **на каждом слое** (в Megatron-LM — 2 all-reduce на трансформерный блок, в attention и MLP). Требуется высокая полоса и низкая латентность: NVLink даёт 600–900 GB/s внутри узла, PCIe 4.0 x16 — ~32 GB/s, межузловой InfiniBand — 25–50 GB/s (т.е. падение в 10–20× при пересечении границы узла), а Ethernet добавляет задержку ~10–50 мкс против <1 мкс у InfiniBand — при мелких сообщениях латентность доминирует над полосой [Source: https://www.spheron.network/blog/multi-node-gpu-training-without-infiniband/]. Именно поэтому тензорный параллелизм в Megatron-style системах практически всегда ограничивают одним узлом (одним DGX, 8 GPU) [Source: https://arxiv.org/pdf/2104.04473].

**4. Гетерогенность.** Классический Megatron-LM tensor-parallel рассчитан на **однородный** набор GPU внутри узла (равные срезы матрицы). Явных встроенных механизмов балансировки под разноскоростные GPU в исходном дизайне нет — не удалось подтвердить по открытым источникам наличие адаптивной нарезки тензоров под гетерогенные GPU в самом Megatron-LM.

**5. Ограничения.** При выходе за пределы узла (через InfiniBand/Ethernet) "даже тщательно оптимизированный дизайн с двумя all-reduce на слой оказывается дорогим", а на Ethernet стоимость коммуникации может превысить время вычисления [Source: https://www.spheron.network/blog/multi-node-gpu-training-without-infiniband/]. Это делает TP практически непригодным для домашней/WAN-сети.

---

## 3. Sequence / Context Parallelism

**1. Что это такое.** Разбиение по измерению последовательности (токенов), а не по слоям или весам — позволяет обрабатывать очень длинный контекст, распределяя вычисление self-attention и активации по устройствам [Source: https://arxiv.org/pdf/2405.07719].

**2. Ключевые реализации.**
- **Megatron-LM Sequence Parallelism** ("Reducing Activation Recomputation in Large Transformer Models" / "Sequence Parallelism: Long Sequence Training from System Perspective") — разбивает по последовательности non-tensor-parallel части (LayerNorm, dropout), коммуникация в основном в self-attention [Source: https://arxiv.org/pdf/2105.13120].
- **DeepSpeed-Ulysses** ("System Optimizations for Enabling Training of Extreme Long Sequence Transformer Models") — шардирует последовательность по GPU, использует all-to-all для транспонирования sequence↔head измерений; "maintaining constant communication volume when sequence length and compute devices are increased proportionally"; масштабируется до контекста свыше 1 млн токенов; степень параллелизма ограничена числом KV-голов [Source: https://arxiv.org/abs/2309.14509].
- **Ring Attention** ("Ring Attention with Blockwise Transformers for Near-Infinite Context", Liu, Zaharia, Abbeel) — организует устройства в кольцо, K/V-блоки передаются point-to-point и полностью перекрываются вычислением blockwise-attention; "without making approximations nor adding any overheads to communication and computation"; степень параллелизма масштабируется линейно с числом устройств [Source: https://arxiv.org/abs/2310.01889].
- **USP** ("A Unified Sequence Parallelism Approach for Long Context Generative AI") объединяет Ulysses и Ring-Attention, снимая ограничения каждого по отдельности и повышая устойчивость "to model architecture and network hardware" [Source: https://arxiv.org/abs/2405.07719].

**3. Требования к сети.** DeepSpeed-Ulysses требует all-to-all коллектив каждый forward-проход (высокие требования к bisection bandwidth, аналогично MoE) [Source: https://arxiv.org/abs/2309.14509]. Ring Attention использует только point-to-point передачу соседям в кольце, полностью маскируемую вычислением — потенциально более терпим к худшей сети, но чувствителен к латентности передачи между соседями по кольцу на каждый блок [Source: https://arxiv.org/abs/2310.01889].

**4. Гетерогенность.** В явном виде не рассчитан на разноскоростные узлы — оба базовых метода (Ulysses, Ring Attention) предполагают равномерное деление последовательности на однородных устройствах; USP отмечает лишь большую "робастность к сетевому железу", не адаптивную балансировку под гетерогенность [Source: https://arxiv.org/abs/2405.07719]. Не удалось подтвердить по открытым источникам наличие динамической балансировки долей последовательности по измеренной скорости узла в этих системах.

**5. Ограничения.** DeepSpeed-Ulysses: "each device must store a complete KV head for the entire sequence length, constraining its degree of sequence parallelism by the number of KV heads" [Source: https://www.emergentmind.com/topics/deepspeed-ulysses-sequence-parallelism]. Ring Attention деградирует при недостаточном перекрытии коммуникации/вычисления на медленных или высоколатентных связях (не подтверждено количественно в открытых источниках для WAN-сценария).

---

## 4. Expert Parallelism (для MoE)

**1. Что это такое.** В Mixture-of-Experts моделях разные "эксперты" (под-сети FFN) размещаются на разных устройствах; токены динамически маршрутизируются (routing) к назначенным им экспертам через all-to-all коммуникацию, что позволяет масштабировать число параметров без пропорционального роста вычислений на токен [Source: https://arxiv.org/abs/2006.16668].

**2. Ключевые реализации.**
- **GShard** — масштабировал sparsely-gated MoE до 600 млрд параметров на 2048 TPU-ядрах, top-2 routing, "explicit expert-parallelism scheme...collaborates with All-to-All communication" [Source: https://arxiv.org/abs/2006.16668].
- **Switch Transformer** (Fedus et al., "Switch Transformers: Scaling to Trillion Parameter Models with Simple and Efficient Sparsity") — top-1 routing вместо top-2 для упрощения и снижения коммуникационных издержек [Source: https://arxiv.org/abs/2101.03961].
- **DeepSpeed-MoE** ("DeepSpeed-MoE: Advancing Mixture-of-Experts Inference and Training to Power Next-Generation AI Scale") — иерархическая all-to-all коммуникация (усиливает intra-node, снижает inter-node трафик), обслуживание триллион-параметрической MoE-модели менее чем за 25 мс при агрегированной пропускной способности памяти 128 ТБ/с [Source: https://arxiv.org/abs/2201.05596].

**3. Требования к сети.** All-to-all вызывается 4 раза за слой (2 в forward, 2 в backward при обучении) и становится главным узким местом при масштабировании [Source: https://proceedings.mlr.press/v162/rajbhandari22a/rajbhandari22a.pdf]. Разрыв в пропускной способности критичен: intra-node NVLink до 900 GB/s против inter-node InfiniBand ~400 Gb/s на линк — разница примерно в 18× [Source: (агрегация по нескольким работам о MoE-инференсе, см. поиск по DeepSpeed-MoE all-to-all bandwidth)].

**4. Гетерогенность.** Базовые GShard/Switch/DeepSpeed-MoE рассчитаны на **однородные** TPU/GPU-кластеры с быстрым interconnect; не заявлена встроенная адаптация под неравную скорость узлов. Не удалось подтвердить по открытым источникам наличие в оригинальных статьях явной heterogeneity-aware балансировки экспертов по измеренной производительности узла (в отличие от heterogeneity-aware систем общего инференса из раздела 5).

**5. Ограничения.** "As the scale and frequency of all-to-all communication grow exponentially, the communication time increases significantly, resulting in reduced overall training efficiency" [Source: https://arxiv.org/pdf/2303.06182]. Балансировка нагрузки между экспертами (routing collapse, unequal token counts) остаётся отдельной проблемой, требующей auxiliary loss или gating regularization (например, Gating Dropout) [Source: https://arxiv.org/pdf/2205.14336].

---

## 5. Heterogeneous Inference (неоднородное железо)

**1. Что это такое.** Класс систем, явно проектируемых для узлов разной вычислительной мощности, объёма памяти и пропускной способности сети (в отличие от разделов 1–4, где однородность — базовое допущение). Решает задачу использования разнородного парка GPU (потребительские карты, разные датацентры, спотовые инстансы) без деградации до скорости самого медленного узла.

**2. Ключевые реализации.**
- **Petals** ("Petals: Collaborative Inference and Fine-tuning of Large Models") — BitTorrent-стиль инференс: модель нарезана на блоки-трансформеры, распределённые по добровольным пирами-серверам; клиент "builds a full graph of client-server and server-server latencies, as well as server inference speeds, to find the fastest chain of servers... via beam search" [Source: https://arxiv.org/abs/2209.01188; https://github.com/bigscience-workshop/petals].
- **SWARM Parallelism** ("SWARM Parallelism: Training Large Models Can Be Surprisingly Communication-Efficient") — peers каждые 300 c измеряют утилизацию по очередям, "peers from the most underutilized pipeline stage will then switch to the most overutilized one"; толерантен к отказам узлов ("as long as there is at least one active participant per stage") [Source: https://arxiv.org/abs/2301.11913].
- **FlexGen** ("High-Throughput Generative Inference of Large Language Models with a Single GPU") — LP-решение для офлоада весов/KV-кэша между GPU/CPU/диском на одном узле, throughput-ориентированный сценарий [Source: https://arxiv.org/abs/2303.06865].
- **HexGen** ("Generative Inference of Large Language Model over Heterogeneous Environment") — асимметричное разбиение по TP+PP для полностью гетерогенной сети и GPU [Source: https://arxiv.org/abs/2311.11514]; развитие **HexGen-2** — disaggregated вариант [Source: https://arxiv.org/abs/2502.07903].
- **LLM-PQ** ("Serving LLM on Heterogeneous Clusters with Phase-Aware Partition and Adaptive Quantization") — смешанно-точностное квантование + phase-aware партиционирование под конкретный микс GPU [Source: https://arxiv.org/pdf/2403.01136].
- **Helix** ("Serving Large Language Models over Heterogeneous GPUs and Network via Max-Flow") — формулирует инференс как задачу max-flow на взвешенном графе, где узлы — GPU-инстансы, а рёбра кодируют и GPU-, и сетевую гетерогенность через их capacity; решается MILP; до 3.3× throughput на кластерах 24-42 узла [Source: https://arxiv.org/abs/2406.01566].
- **Hetis** ("Serving LLMs in Heterogeneous GPU Clusters with Fine-grained and Dynamic Parallelism") — сочетает статический поиск стратегии параллелизма при старте с динамическим онлайн-диспетчером attention-голов в рантайме [Source: https://arxiv.org/html/2509.08309].

**3. Требования к сети.** Крайне разнятся между системами: Petals/SWARM ориентированы на "домашние"/интернет-соединения (~500 Mb/s достаточно для приемлемой утилизации) [Source: https://arxiv.org/abs/2301.11913]; Hetis тестировался на 100 Gbps LAN [Source: https://arxiv.org/html/2509.08309]; MILP-based работа по cost-efficiency тестировалась на Ethernet 5 Gb/s, ограничивая тензорный параллелизм только одной машиной именно из-за полосы [Source: https://arxiv.org/html/2502.00722v2].

**4. Гетерогенность — КЛЮЧЕВОЙ пункт.** Все перечисленные системы **явно** рассчитаны на гетерогенность, но с разными механизмами балансировки:
- **Petals/SWARM**: динамическая (в рантайме) балансировка по измеренному throughput блоков; "All nodes periodically check if launching a rebalancing procedure would significantly improve the overall throughput" [Source: https://arxiv.org/abs/2301.11913]. "Square-Cube Law": вычисление растёт как O(n³), коммуникация как O(n²) — GPT-3-scale модель достигает 82.1% утилизации GPU при 500 Mb/s [Source: https://arxiv.org/abs/2301.11913].
- **LLM-PQ**: статическое (офлайн) ILP-решение на основе профилированного времени выполнения decoder-слоя на каждом типе GPU, через регрессионную интерполяцию по точкам замера, а не теоретические FLOPs [Source: https://arxiv.org/pdf/2403.01136].
- **Helix**: капасити рёбер/узлов max-flow графа заданы через профилирование реального железа; вычисляется статически, без рекомпьютации в рантайме [Source: https://arxiv.org/pdf/2406.01566].
- **Hetis**: гибрид — стратегия параллелизма статична после стартового поиска, но диспетчеризация attention-голов **динамически** пересчитывается при отклонении измеренного времени attention от идеального более чем на порог Θ (по умолчанию 50%) [Source: https://arxiv.org/html/2509.08309].

**5. Ограничения.**
- Petals: "cannot fully account for device heterogeneity or network bottlenecks, often leading to under-utilization of available resources" (в независимых обзорах) [Source: https://arxiv.org/pdf/2509.26182]; SWARM явно требует "similar blocks", "optimization strategies are left as future work" [Source: https://arxiv.org/abs/2301.11913].
- LLM-PQ: рассчитан на офлайн-задачи с заранее известной длиной промпта/генерации [Source: https://arxiv.org/pdf/2403.01136].
- Hetis: деградация до "up to 6.9%" latency при ошибке параметров профилирования ±20% [Source: https://arxiv.org/html/2509.08309].
- MILP cost-efficiency: статическое планирование, TP ограничен одной машиной из-за 5 Gb/s [Source: https://arxiv.org/html/2502.00722v2].

---

## 6. Disaggregated Serving (разделение prefill/decode на разные пулы железа)

**1. Что это такое.** Prefill (compute-bound) и decode (memory-bandwidth-bound) имеют разные ресурсные профили; их размещение на одном GPU вызывает interference. Disaggregation разносит фазы на отдельные, независимо масштабируемые пулы GPU [Source: https://arxiv.org/abs/2311.18677; https://arxiv.org/abs/2401.09670].

**2. Ключевые реализации.**
- **Splitwise** (Patel et al., ISCA 2024) — первым явно предложил разнести две фазы по разным машинам [Source: https://arxiv.org/abs/2311.18677].
- **DistServe** — "places the two phases according to the serving cluster's bandwidth to minimize the communication caused by disaggregation" [Source: https://arxiv.org/abs/2401.09670].
- **Mooncake** (Kimi/Moonshot AI) — KVCache-centric disaggregation, использует недогруженные CPU/DRAM/SSD как разделяемый кэш KV; до 525% прироста throughput в отдельных сценариях [Source: https://arxiv.org/abs/2407.00079].
- **TetriInfer** — фикс-размерные чанки промпта плюс двухуровневый scheduler; ресурсы −38%, TTFT −97%, JCT −47% [Source: https://arxiv.org/abs/2401.11181].

**3. Требования к сети.** Центральный результат DistServe: для OPT-66B с 512 токенами KV ≈1.13 ГБ; при 10 запросах/с требуется ~90Gbps, чтобы сделать издержки передачи "невидимыми"; современные кластеры с InfiniBand (800 Gbps) это перекрывают [Source: https://arxiv.org/html/2401.09670v3]. При 25 Gbps стенде DistServe переключается на алгоритм с обязательной колокацией prefill/decode одной стадии на одном узле (NVLink вместо межузлового линка); даже так передача KV — "less than 0.1% of the total latency" для OPT-175B [Source: https://arxiv.org/html/2401.09670v3].

**4. Гетерогенность.** DistServe/Splitwise/Mooncake/TetriInfer используют возможность назначать под prefill и decode **разные типы GPU** — гетерогенность по назначению роли, не по измеренной скорости узла в реальном времени. У DistServe выбор алгоритма размещения основан на измеренной полосе кластера, но это конфигурационное, не рантайм-решение [Source: https://arxiv.org/html/2401.09670v3].

**5. Ограничения.** Disaggregation вносит "dependency between prefill and decoding instances", создавая "risk of fault propagation" [Source: https://arxiv.org/html/2401.09670v3].

---

## 7. Distributed KV Cache

**1. Что это такое.** KV-кэш выносится за пределы памяти одного GPU и управляется как разделяемый пул между инстансами — для переиспользования (context caching), disaggregated serving и длинного контекста [Source: https://arxiv.org/abs/2406.17565; https://arxiv.org/abs/2401.02669].

**2. Ключевые реализации.**
- **vLLM / PagedAttention** — постраничный KV-кэш, 2–4× прирост throughput [Source: https://arxiv.org/abs/2309.06180].
- **Mooncake** — KVCache-centric scheduler, распределённый кэш поверх CPU/DRAM/SSD кластера [Source: https://arxiv.org/abs/2407.00079].
- **MemServe** — MemPool, эластичный распределённый пул памяти + KV-кэшей, глобальный scheduler с "global prompt tree-based locality-aware policy" [Source: https://arxiv.org/abs/2406.17565].
- **Infinite-LLM / DistAttention** — разбивает KV на мелкие юниты, раздельное масштабирование attention; контексты до 2000K токенов на 32×A100, прирост 1.35–3.4× [Source: https://arxiv.org/abs/2401.02669].

**3. Требования к сети.** NetKV: для Llama-3-70B при 128K контексте совокупный KV ≈40 ГБ, при TP=4 — ~10 ГБ на пару prefill→decode GPU; при 32K-токенном запросе время передачи достигает "~2 seconds — easily dominating the TTFT budget" [Source: https://arxiv.org/html/2606.03910v1]. См. также 90 Gbps порог DistServe [Source: https://arxiv.org/html/2401.09670v3].

**4. Гетерогенность.** MemServe и Mooncake — гетерогенность по **типу памяти** (GPU HBM/CPU DRAM/SSD), не по скорости узлов. Пер-узловая адаптация под разноскоростные сети — не удалось подтвердить как основную конструктивную идею.

**5. Ограничения.** PagedAttention — overhead на непрямую адресацию [Source: https://arxiv.org/abs/2309.06180]. Передача KV через медленную сеть может доминировать в TTFT [Source: https://arxiv.org/html/2606.03910v1].

---

## 8. Prefill/Decode Separation (как техника планирования)

**1. Что это такое.** Разделение на фазы для целей планирования даже без физического разнесения: при совместном батчинге длинные prefill-запросы "тормозят" (stall) decode-запросы [Source: https://arxiv.org/abs/2308.16369].

**2. Ключевые реализации.**
- **Sarathi** — chunked-prefills, "piggyback" с decode-шагами в одном батче [Source: https://arxiv.org/abs/2308.16369].
- **Sarathi-Serve** (OSDI'24) — stall-free scheduling; до 3.7× serving capacity для Yi-34B [Source: https://arxiv.org/abs/2403.02310].
- **TetriInfer** — "prefill-only" чанки с физически раздельными инстансами [Source: https://arxiv.org/abs/2401.11181].

**3. Требования к сети.** Если фазы физически разнесены — как в разделе 6. Если совмещены (Sarathi-Serve) — межузловой трафик не требуется [Source: https://arxiv.org/abs/2403.02310].

**4. Гетерогенность.** Sarathi-Serve не заявлен как heterogeneity-aware. Disaggregated варианты — гетерогенность по роли GPU.

**5. Ограничения.** Trade-off между размером чанка и throughput/TTFT [Source: https://arxiv.org/abs/2403.02310].

---

## 9. Scheduler-архитектуры для LLM serving

**2. Ключевые реализации.**
- **Orca** (OSDI'22) — iteration-level scheduling ("continuous batching"): батч пересобирается на каждой итерации [Source: https://www.usenix.org/system/files/osdi22-yu.pdf].
- **vLLM** — PagedAttention + preemptive iteration-level scheduling [Source: https://arxiv.org/abs/2309.06180].
- **Llumnix** (OSDI'24) — глобальный + локальные scheduler'ы, живая миграция запросов между инстансами (append-only KV cache); метрика "virtual usage"; до 15× быстрее P99 prefill latency [Source: https://arxiv.org/abs/2406.03243].
- **GORGO** ("Online Tuning for Cross-Region Network-Aware LLM Serving") — TTFT = W_rtt·T_network(RTT) + W_queue·T_queue + T_prefill, веса подстраиваются онлайн (1+1)-ES по p95 TTFT; RTT — EWMA раз в 30 с; разброс RTT между регионами от ~10 мс до ~1 с [Source: https://arxiv.org/abs/2602.11688].
- **NetKV** — "network cost oracle": tier-классификация, полоса, базовая латентность, динамический congestion factor; B_eff = [B_τ(1−c_τ)]/[1+n_inflight^τ]; RTT/jitter/packet-loss явно НЕ моделируются, ставка на RDMA congestion control (DCQCN) [Source: https://arxiv.org/html/2606.03910v1].

**5. Ограничения.** GORGO: не моделирует jitter и packet loss, только RTT [Source: https://arxiv.org/pdf/2602.11688]. NetKV: статичные tier-оценки [Source: https://arxiv.org/html/2606.03910v1]. Llumnix: живая миграция опирается на append-only KV cache [Source: https://arxiv.org/abs/2406.03243].

---

## Ответы на дополнительные вопросы

### Вопрос 1: динамический пересчёт точки разреза pipeline по измеренной производительности

Обнаружены системы:
- **Petals / SWARM Parallelism** — блоки динамически переназначаются серверам в рантайме по измеренному throughput: "All nodes periodically check if launching a rebalancing procedure would significantly improve the overall throughput. If it is the case, they switch layers until the throughput becomes near-optimal" [Source: https://arxiv.org/abs/2301.11913; https://github.com/bigscience-workshop/petals]. Клиент строит граф задержек и throughput и выбирает цепочку через beam search per-request [Source: https://arxiv.org/abs/2209.01188].
- **Hetis** — стратегия статична после стартового поиска, но диспетчеризация attention-heads динамически пересчитывается при отклонении > Θ [Source: https://arxiv.org/html/2509.08309].
- **Helix**, **LLM-PQ**, **HexGen** — измеренные характеристики, но решение **статическое** (offline MILP/ILP) [Source: https://arxiv.org/pdf/2406.01566; https://arxiv.org/pdf/2403.01136].

Итог: наиболее чёткий пример — **Petals / SWARM Parallelism**.

### Вопрос 2: балансировка по измеренному времени вычисления каждого слоя на каждом узле (per-layer, per-node cost model)

- **LLM-PQ**: профилирует execution time decoder-слоя на каждом типе GPU, регрессионная cost-модель — но **офлайн**, для статического ILP [Source: https://arxiv.org/pdf/2403.01136].
- **Hetis**: параметрическая per-device cost-модель attention: τᵢ(t) = aᵢ·hᵢ(t) + bᵢ·gᵢ(t) + cᵢ (коэффициенты fitted offline); точность 93.8% (вычисление), 92.4–96.1% (передача); используется **в рантайме** [Source: https://arxiv.org/html/2509.08309].
- **Helix**: профилирование реального железа, применяется офлайн [Source: https://arxiv.org/pdf/2406.01566].

Итог: **по состоянию на момент исследования полностью строгий "per-layer × per-node" cost model, обновляемый в реальном времени и напрямую управляющий pipeline layer cut, среди изученных источников не обнаружен** — ближайшие аналоги (Hetis, LLM-PQ, Helix) не сочетают все три свойства одновременно (per-layer granularity + per-node measured + определяет границу pipeline-стадии в реальном времени).

### Вопрос 3: scheduler'ы, учитывающие RTT/jitter/packet loss как вход планирования

- **GORGO** — явно использует **RTT** (EWMA, раз в 30 с) как компонент функции стоимости; **jitter и packet loss не включены** [Source: https://arxiv.org/pdf/2602.11688].
- **NetKV** — не RTT напрямую, а tier-модель + congestion factor (раз в секунду); RTT/jitter/packet-loss не first-class сигналы [Source: https://arxiv.org/html/2606.03910v1].
- **Solyx AI Grid**, **SLA-Aware Device-RAN-Cloud** — темы подтверждены по заголовкам, детали не проверены [Source: https://arxiv.org/html/2606.15050; https://arxiv.org/html/2602.23722].

Итог: явный учёт RTT — **GORGO**. **Jitter и packet loss как явные, отдельно смоделированные величины в scheduler'е LLM serving — по состоянию на момент исследования аналогов среди изученных источников не обнаружено.**

---

## Сводная таблица

| Концепция | Типичное требование к сети | Поддержка гетерогенных узлов |
|---|---|---|
| Pipeline parallelism (GPipe/PipeDream/Megatron) | Передача активаций на границе стадий; маскируется при m≥4p микробатчей | Нет (базовый дизайн) |
| Tensor parallelism (Megatron-LM) | All-reduce **на каждом слое**; практически требует NVLink; деградация 10–20× за пределами узла | Нет |
| Sequence/context parallelism (Ring Attention, Ulysses) | Ulysses — all-to-all каждый forward; Ring — P2P по кольцу | Нет |
| Expert parallelism (GShard/Switch/DeepSpeed-MoE) | All-to-all 4× за слой | Нет |
| Heterogeneous inference (Petals/SWARM, Helix, LLM-PQ, HexGen, Hetis) | От ~500 Mb/s (Petals) до 100 Gbps LAN (Hetis) | **Да** — целевой сценарий |
| Disaggregated serving (DistServe/Splitwise/Mooncake/TetriInfer) | ~90 Gbps порог "невидимости" KV-transfer; работает при 25 Gbps ценой колокации | Частично — разные GPU под роли |
| Distributed KV cache (Mooncake/MemServe/Infinite-LLM) | KV-transfer до секунд при большом контексте | Частично — по типу памяти |
| Prefill/decode separation (Sarathi-Serve) | Нет межузловых требований (если совмещено) | Нет |
| Scheduler (Orca/vLLM/Llumnix/GORGO/NetKV) | Orca/vLLM — нет; GORGO/NetKV — канал миграции | Частично/Да — GORGO (RTT), NetKV (tier) |

**Примечание по полноте:** Solyx AI Grid и SLA-Aware Device-RAN-Cloud найдены как релевантные по теме, но полное содержание не извлечено — нужен отдельный проход при необходимости.
