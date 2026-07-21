# Часть 3. Методы спекулятивного декодирования

SpecInfer, Medusa, EAGLE/-2/-3, Lookahead Decoding, стандартный SD, multi-draft SD, PARD, n-gram SD. Только открытые источники (arXiv, GitHub, документация); где деталь не подтверждена — указано явно.

---

## 1. Стандартное (одномодельное) спекулятивное декодирование

Источники: Leviathan et al., «Fast Inference from Transformers via Speculative Decoding», arXiv:2211.17192; Chen et al. (DeepMind), «Accelerating Large Language Model Decoding with Speculative Sampling», arXiv:2302.01318.

1. **Идея**: малая draft-модель генерирует γ токенов; target проверяет их за один параллельный forward pass; модифицированный rejection sampling принимает совпадающий префикс — итоговое распределение точно совпадает с target [Source: arXiv:2211.17192; arXiv:2302.01318].
2. **Обучение**: не требуется, off-the-shelf модели [Source: arXiv:2211.17192].
3. **Структура кандидатов**: строго линейная цепочка [Source: оба].
4. **Верификация**: один forward pass, обычная каузальная маска (кандидаты линейны) [Source: arXiv:2211.17192].
5. **Несколько источников**: нет, один drafter.
6. **Распределённость**: у Chen et al. «distributed setup» = тензорное шардирование target-модели (Chinchilla 70B на 16 TPU v4), НЕ сетевое разнесение draft/target: «for distributed setups it is insufficient to naively choose a small model as the draft... Serving a 7B model on 16 TPUs actually increases the latency» [Source: arXiv:2302.01318]. У Leviathan — совместное размещение [Source: arXiv:2211.17192].
7. **Ускорение**: 2–3× (T5-XXL); 2–2.5× (Chinchilla 70B) [Source: оба].
8. **Verify cost vs acceptance**: формализовано. Ожидаемое ускорение: `(1 − α^(γ+1)) / [(1 − α)(γc + 1)]` (α — вероятность принятия, γ — длина спекуляции, c — отношение стоимости draft/target шага) — рост γ полезен только пока α^γ не мал [Source: arXiv:2211.17192].

---

## 2. SpecInfer

Miao, Oliaro et al., arXiv:2305.09781; ASPLOS'24.

1. **Идея**: один или несколько малых speculative models (SSM) формируют **token tree**, LLM работает как «tree verifier» [Source: arXiv:2305.09781].
2. **Обучение**: SSM — готовые малые модели; опциональный «boost-tuning» («unsupervised... adaptive boosting» для выравнивания выходов SSM с LLM) [Source: https://arxiv.org/html/2305.09781].
3. **Структура**: дерево со статической стратегией расширения `⟨k1,...,km⟩`; в экспериментах `⟨1,1,3,1,1,1,1,1⟩`; деревья нескольких SSM **сливаются** [Source: там же].
4. **Верификация**: «topology-aware causal mask» — KV-кэш в топологии дерева, каузальная маска по топологии, batched-верификация всех ветвей за один forward pass [Source: там же].
5. **Несколько источников**: **да** — ансамбль boost-tuned SSM со слиянием деревьев [Source: там же].
6. **Распределённость**: TP внутри узла + PP между узлами — для **target-модели**; offloading CPU↔GPU. Сетевое разнесение SSM и LLM **не найдено** — общий serving-стек [Source: там же].
7. **Ускорение**: 1.5–2.8× (distributed), 2.6–3.5× (offloading), 1.2–1.5× против sequence-based SD. Железо: AWS g5.12xlarge (4× A10 24GB). Модели: LLaMA-7B...65B; SSM: LLaMA-68M, OPT-125M [Source: там же].
8. **Verify cost vs acceptance**: ограниченно; «dynamically expanding a token tree from an SSM is an opening research problem beyond the scope of this paper» — одна фиксированная конфигурация дерева, кривых компромисса нет [Source: там же].

---

## 3. Medusa

Cai, Li et al., arXiv:2401.10774; https://github.com/FasterDecoding/Medusa.

1. **Идея**: дополнительные «decoding heads» на последнем скрытом состоянии target-модели, каждая предсказывает токен на k позиций вперёд; кандидаты — в дерево, верификация одним forward pass с tree-attention [Source: arXiv:2401.10774].
2. **Обучение**: обязательно. Medusa-1 — головы поверх замороженного backbone; Medusa-2 — совместно с backbone [Source: https://arxiv.org/html/2401.10774].
3. **Структура**: дерево; базово — декартово произведение top-s_k голов; оптимизированно — **разреженное дерево** по калибровочному датасету (64 узла лучше плотных 256) [Source: там же].
4. **Верификация**: один forward pass, tree-attention («each token only accesses its predecessors»); rejection sampling или **typical acceptance** [Source: там же].
5. **Несколько источников**: нет — только головы одной модели; авторы явно противопоставляют: «employing multiple draft models can be cumbersome» [Source: там же].
6. **Распределённость**: batch size 1, локальный хостинг; головы прикреплены к скрытым состояниям — та же машина [Source: там же].
7. **Ускорение**: Medusa-1 >2.2×; Medusa-2 2.3–3.6×; A100, MT-Bench [Source: там же].
8. **Verify cost vs acceptance**: явно (Fig. 4b): «a more complex tree can improve acceleration... at the cost of speed due to intensive matrix multiplications»; ускорение растёт логарифмически и выходит на плато [Source: там же].

---

## 4. EAGLE

Li et al., arXiv:2401.15077.

1. **Идея**: авторегрессия на уровне **признаков second-to-top-layer** target-модели; неопределённость снимается подачей токенов, сдвинутых на шаг [Source: arXiv:2401.15077].
2. **Обучение**: да — «Autoregression Head» (FC + decoder-слой), 0.24–0.99B параметров, 1–2 дня на 4×A100 для 70B [Source: https://arxiv.org/html/2401.15077].
3. **Структура**: дерево фиксированной формы (эмпирически задана), m forward passes drafter'а → дерево глубины m [Source: там же].
4. **Верификация**: один forward pass, tree attention, «Multi-round speculative sampling» — точное распределение сохраняется [Source: там же].
5. **Несколько источников**: не обсуждается.
6. **Распределённость**: «lightweight plug-in» на том же устройстве, что и target [Source: там же].
7. **Ускорение**: LLaMA2-Chat-70B 2.7–3.5×; в 1.7–2.1× быстрее Lookahead, в 1.5–1.6× быстрее Medusa [Source: там же].
8. **Verify cost vs acceptance**: частично: tree attention даёт +0.6–0.8 к длине принятия против цепочки; при росте batch size ускорение падает (3.01×@bs=1 → 2.40×@bs=4) [Source: там же].

---

## 5. EAGLE-2

Li et al., arXiv:2406.16858.

1. **Идея**: **контекстно-зависимое динамическое дерево** — confidence-скор drafter'а хорошо калиброван и приближает вероятность принятия [Source: arXiv:2406.16858].
2. **Обучение**: не требуется дополнительно — drafter из EAGLE-1, меняется только алгоритм построения дерева [Source: https://arxiv.org/html/2406.16858].
3. **Структура**: динамическое дерево, 2 фазы: **Expand** (top-k узлов по value `Vi ≈ ∏ c_j`) + **Rerank** (top-m по value). Бюджет: 60/50/48 draft-токенов для 7B/13B/70B target, глубина 6 [Source: там же].
4. **Верификация**: tree attention, один forward pass, точное распределение [Source: там же].
5. **Несколько источников**: один drafter.
6. **Распределённость**: не найдено [Source: там же].
7. **Ускорение**: 3.05–4.26×, на 20–40% быстрее EAGLE-1 [Source: там же].
8. **Verify cost vs acceptance**: косвенно — меньший бюджет дерева (48) для более дорогой 70B target; «inputting too many tokens at once can slow down the draft model's forward pass» [Source: там же].

---

## 6. EAGLE-3

Li et al., arXiv:2503.01840.

1. **Идея**: прямое предсказание токенов (отказ от feature prediction), слияние признаков нескольких слоёв target; «training-time test» [Source: arXiv:2503.01840].
2. **Обучение**: да; ключевой вклад — scaling law по данным (~8× объёма данных EAGLE) [Source: https://openreview.net/pdf?id=4exx1hUffq].
3. **Структура**: динамическое дерево EAGLE-2, глубина увеличена с 6 до 8 [Source: обзорные материалы arXiv:2503.01840].
4. **Верификация**: tree attention, как EAGLE-2.
5. **Несколько источников**: один drafter.
6. **Распределённость**: не найдена; эксперименты на одном GPU [Source: openreview].
7. **Ускорение**: до 6.5×, ~1.4× выше EAGLE-2; в SGLang на H100: 1.38× throughput при batch=64 [Source: обзорные материалы].
8. **Verify cost vs acceptance**: обсуждается устойчивость к росту batch size; явной кривой «размер дерева vs цена» нет.

---

## 7. Lookahead Decoding

Fu et al., arXiv:2402.02057; https://lmsys.org/blog/2023-11-21-lookahead-decoding/; https://github.com/hao-ai-lab/LookaheadDecoding.

1. **Идея**: без draft-модели; декодирование как решение нелинейной системы (метод Якоби); из траектории якобиевых итераций извлекаются n-граммы, кэшируются и параллельно верифицируются самой target-моделью [Source: arXiv:2402.02057].
2. **Обучение**: нет — «without needing auxiliary models or data stores» [Source: там же].
3. **Структура**: непересекающиеся n-граммы (не дерево), параметр G ограничивает число кандидатов [Source: там же].
4. **Верификация**: специальная маска — lookahead-ветвь и verification-ветвь не видят друг друга, обе за один decoding step [Source: там же].
5. **Несколько источников**: drafter отсутствует как сущность.
6. **Распределённость**: **Lookahead Parallelism (LP)** — полная копия модели на каждом GPU, распределение ветвей n-грамм по GPU, «near-zero communication per step», до 4× на 8 GPU — но это разнесение одной процедуры по GPU **одного узла/DGX**, не draft/target через сеть [Source: там же].
7. **Ускорение**: 1.5–2.3× (1 GPU); до 4× (8 GPU LP, code completion) [Source: там же].
8. **Verify cost vs acceptance**: наиболее детальный анализ из всех методов: «linearly reduce the number of decoding steps according to per-step log(FLOPs)» — линейное сокращение шагов требует экспоненциального роста FLOPs (×120 для 7B, ×80 для 13B, ×56 для 34B); выгодно потому что декодирование memory-bandwidth-bound; предупреждение: «Running in compute-bound environments... may cause slowdowns»; на RTX 3090 выигрыш ~30% против >50% на A100 [Source: там же].

---

## 8. Multi-draft speculative decoding

«Towards Optimal Multi-draft Speculative Decoding», arXiv:2502.18779; «Multi-Draft Speculative Sampling: Canonical Decomposition and Theoretical Limits», arXiv:2410.18234.

1. **Идея**: несколько draft-продолжений на позицию (несколько сэмплов одной модели или несколько моделей), совместная верификация максимизирует вероятность принятия хотя бы одного; оптимальная верификация — задача **оптимального транспорта** [Source: arXiv:2502.18779].
2. **Обучение**: не требуется [Source: оба].
3. **Структура**: набор независимых последовательностей (не дерево); 2410.18234 явно рассматривает **несколько разных draft-моделей** [Source: arXiv:2410.18234].
4. **Верификация**: OTP-формализация; алгоритмы RRS, K-SEQ, «greedy verification»; каноническая двухшаговая схема (importance-weighted выбор + классическая верификация) [Source: оба].
5. **Несколько источников**: да, ядро направления [Source: arXiv:2410.18234].
6. **Распределённость**: не обсуждается; эксперименты на одном GPU (A100 80GB / RTXA6000) [Source: оба].
7. **Ускорение**: скромное: ~1.13× (MT-bench, 2 drafts); прирост в основном в acceptance rate (71–78%); block efficiency 2.13–2.28 против 1.76–2.08 baseline [Source: оба].
8. **Verify cost vs acceptance**: явно: «the combined cost of auto-regressively sampling from the draft model and parallel verification via the target model should be smaller than auto-regressively sampling from the target model»; зазор до оптимума растёт с числом драфтов (убывающая отдача) [Source: оба].

---

## 9. PARD

«PARD: Accelerating LLM Inference with Low-Cost PARallel Draft Model Adaptation», arXiv:2504.18583.

1. **Идея**: draft-модель предсказывает K токенов **за один forward pass** через mask-токены `m_k` [Source: https://arxiv.org/html/2504.18583].
2. **Обучение**: да; **Conditional Drop-token (COD)** снижает стоимость обучения в ~3× [Source: там же].
3. **Структура**: линейный блок из K токенов (не дерево) [Source: там же].
4. **Верификация**: один forward pass по [prefix, x_n..x_{n+K−1}]; `T_PARD = T_D + T_T` против `K×T_D + T_T` [Source: там же].
5. **Несколько источников**: не ансамбль, но **target-независимость** — один drafter на всё семейство target-моделей (Qwen2.5-0.5B → 3B/7B/14B) [Source: там же].
6. **Распределённость**: не рассматривается; один GPU (A100-40GB, vLLM) [Source: там же].
7. **Ускорение**: 3.18–4.44×; в 1.72× быстрее vanilla SD; в 1.15× быстрее EAGLE-3 на LLaMA3.1-8B [Source: там же].
8. **Verify cost vs acceptance**: явно, с цифрами: bandwidth верификации у EAGLE растёт с K (5.94→11.88 GB/s), у PARD — постоянный ~2.48 GB/s; стоимость обучения в 7× ниже EAGLE, в 10× ниже EAGLE-3 [Source: там же].

*(Есть также PARD-2, arXiv:2605.08632 — до 6.94×, 1.9× над EAGLE-3; глубоко не разбиралась.)*

---

## 10. N-gram speculative decoding (Prompt Lookup Decoding)

https://github.com/apoorvumang/prompt-lookup-decoding; llama.cpp: https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md.

1. **Идея**: draft-модель заменяется строковым n-gram-сопоставлением по промпту/истории [Source: PLD repo]. Варианты llama.cpp: `ngram-simple`, `ngram-map-k`, `ngram-map-k4v`, `ngram-mod` [Source: llama.cpp docs].
2. **Обучение**: нет [Source: PLD repo].
3. **Структура**: линейная последовательность k токенов [Source: оба].
4. **Верификация**: стандартная одномодельная схема [Source: PLD repo].
5. **Несколько источников**: одно совпадение [Source: PLD repo].
6. **Распределённость**: не обсуждается [Source: оба].
7. **Ускорение**: 2–4× на input-grounded задачах (суммаризация, context-QA); llama.cpp — acceptance rate 0.576/0.703 в примерах [Source: оба].
8. **Verify cost vs acceptance**: формального разбора не найдено — не удалось подтвердить по открытым источникам.

---

## Сводная таблица

| Метод | Обучение | Дерево | Несколько draft | Batched/tree verify | Distributed draft-target | Ускорение |
|---|---|---|---|---|---|---|
| Стандартный SD | ❌ | ❌ | ❌ | ✅ | ❌ | 2–3× |
| SpecInfer | ⚠️ (boost-tuning) | ✅ | ✅ (ансамбль SSM) | ✅ (tree mask) | ⚠️ (только target) | 1.5–2.8× |
| Medusa | ✅ | ✅ | ❌ | ✅ | ❌ | 2.2–3.6× |
| EAGLE | ✅ | ✅ | ❌ | ✅ | ❌ | 2.7–3.5× |
| EAGLE-2 | ❌ (drafter из E-1) | ✅ (динамич.) | ❌ | ✅ | ❌ | 3.05–4.26× |
| EAGLE-3 | ✅ | ✅ (динамич.) | ❌ | ✅ | ❌ | до 6.5× |
| Lookahead | ❌ | ❌ (n-граммы) | ❌ (нет drafter) | ✅ | ⚠️ (LP по GPU узла) | 1.5–2.3× (4× LP) |
| Multi-draft SD | ❌ | ❌ | ✅ | ✅ (OT-верификация) | ❌ | ~1.13× |
| PARD | ✅ (COD) | ❌ | ⚠️ (1 drafter на семейство) | ✅ | ❌ | 3.18–4.44× |
| N-gram (PLD) | ❌ | ❌ | ❌ | ✅ | ❌ | 2–4× |

---

## Ключевой вопрос: обсуждает ли хоть один из 10 методов drafter на отдельном сетевом узле?

**Нет. Ни один из десяти рассмотренных методов не проектирует разнесение draft- и target-вычислений по разным физическим сетевым узлам.** Наиболее близкие обсуждения (Chen et al., SpecInfer) касаются размещения **target-модели** на нескольких чипах одного кластера, не сетевого разнесения drafter/verifier.

**Однако существует отдельная, смежная линия работ** (не входившая в заданный список), целенаправленно решающая именно эту задачу:
- «Distributed Speculative Inference (DSI)» (ICLR 2025) [Source: https://proceedings.iclr.cc/paper_files/paper/2025/file/b36554b97da741b1c48c9de05c73993e-Paper-Conference.pdf]
- «SLED: A Speculative LLM Decoding Framework for Efficient Edge Serving» (arXiv:2506.09397)
- «DiP-SD: Distributed Pipelined Speculative Decoding for Efficient LLM Inference at the Edge» (arXiv:2604.20919)
- «Speculative Decoding in Decentralized LLM Inference» (arXiv:2511.11733)
- «Fast Collaborative Inference via Distributed Speculative Decoding» (arXiv:2512.16273)
- «WISV: Wireless-Informed Semantic Verification» (arXiv:2604.17701)
- «ConfigSpec» (arXiv:2604.09722)

Эти работы явно моделируют RTT/latency сети между edge-drafter и cloud/edge-verifier — они найдены как существующие, но глубоко не разбирались в рамках этого отчёта. **Любые утверждения о новизне распределённой спекуляции должны сначала пройти проверку против этого списка.**
