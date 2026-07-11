# Project Vision — Distributed Inference Network on Commodity Hardware

**Type:** Vision / mission definition + physics-grounded scaling law + architecture gap analysis
**Status:** Adopted
**Supersedes framing of:** `docs/TARGET_70B_GOAL_AND_FEASIBILITY.md` (70B remains milestone rung L3, not the end goal)

---

## 1. Mission

A distributed inference network for people and companies who **cannot afford dedicated AI clusters** but have machines with spare compute: a corporate Windows PC with 6 GB RAM is a valid node, and so is a powerful gaming rig or a server. Pooled together, these machines run models that **no single one of them could run at all**.

**The product is capacity, not speed.** Research 17 (finding F1) proved this is not a limitation but the correct physics: a distributed pipeline can never beat a single node that fits the model — but for models that fit on *no* node, it is the only option, and its throughput *exceeds* any hypothetical single commodity machine (70B analysis, `TARGET_70B_GOAL_AND_FEASIBILITY.md` §2). The project should never be marketed or measured as "as fast as local"; it is measured as **"models you could not run before, at usable speed and near-zero hardware cost."**

## 2. The scaling law (what physics guarantees and what it doesn't)

Two quantities scale differently with node count:

| Quantity | Scaling | Formula |
|----------|---------|---------|
| **Capacity** (max model size) | **~linear** in nodes | `Σ fast_mem_i − KV − buffers − replication overhead` |
| **Single-stream speed** | **bandwidth-weighted, not node-count** | `T_token = Σ share_i / BW_i + F(N)` |

Consequences that must drive design:

1. **More nodes → bigger models: true.** Every node adds its memory to capacity.
2. **More nodes → faster tokens: false.** Speed is set by *which bytes sit on which bandwidth*. One weak node holding a large share dominates the token: a 6 GB DDR office PC (~25–30 GB/s effective) holding 5 GB of weights adds **~170–200 ms/token** — more than an entire 3-GPU-node pipeline. The same node holding 1 GB adds ~35 ms.
3. **Therefore the planner is the product.** Weak nodes are admitted **for capacity** and given the *smallest byte share the memory constraint allows*; fast nodes are filled first (Task 17.5 rules generalize from 3 nodes to N).
4. **Fixed overhead F(N) grows with hops** — on LAN negligible (~0.3 ms/hop), on WAN 20–80 ms/hop. The Task 17.x overhead reductions and Task 19 speculation (which amortizes F over k tokens) are what keep many-node pipelines usable.
5. **Throughput (not latency) recovers via batching:** many concurrent streams share the same weight reads; a capacity network serving multiple users amortizes both F and weak-node latency. Aggregate tokens/s scales where single-stream tok/s cannot.

**Honest example [model]:** 10 office PCs (6 GB, ~28 GB/s each) pooled = ~40 GB usable → runs a 70B Q3 that none could run; single-stream ≈ `34 GB / 28 GB/s ≈ 1.2 s/token ≈ 0.8 tok/s`. Add one used RTX 3090 (24 GB, ~700 GB/s) holding 20 GB of it → ~0.5 s/token. Batch 8 streams → ~10–14 tok/s aggregate [est]. This is the shape of the economics: **weak nodes buy capacity; a few strong nodes buy speed; batching buys throughput.**

## 3. Node classes (planner vocabulary)

| Class | Example | Contribution | Placement policy |
|-------|---------|--------------|------------------|
| **Anchor** | GPU rig, server w/ fast VRAM | speed + capacity | fill first, largest shares, endpoint roles (entry/final: heaviest per Study §6.3) |
| **Capacity** | office PC 6–16 GB DDR, CPU-only | memory | minimal shares, middle-stage layer ranges only (middle proved cheapest per-byte, Study F4) |
| **Utility** | any low-mem machine | tokenizer / sampler / draft-model host / control plane | no layer bytes (Task 11 runtime roles) |

The Task 11 role architecture (TOKENIZER / EMBEDDING / PIPELINE_STAGE / OUTPUT_HEAD / SAMPLER) anticipated exactly this — weak machines take Utility roles instead of being excluded.

## 4. Success metrics (network era)

1. **Largest servable model** per pooled cluster (capacity ladder, extended past 70B as nodes are added).
2. **% of computed cluster ceiling** for decode (per `PERFORMANCE_METRICS_SPEC.md` discipline — ceiling recomputed per actual topology).
3. **Aggregate tokens/s at N concurrent streams** (new — requires batching).
4. **Time-to-first-serve** for a new model on a warm network (sync + session, Tasks 18.1/18.2).
5. **Survival**: generation continues (or resumes) when a node dies mid-session (new — see §5).

## 5. Gap analysis — what the vision requires that the current architecture lacks

| Requirement | Today | Gap severity | Candidate task |
|-------------|-------|--------------|----------------|
| **Fault tolerance / node churn** | worker death kills the session (qwen8b "failed to connect to ctrl port"); no retry, no re-route | **Critical** — office PCs reboot daily; a network of volunteers cannot assume static membership | **Task 20.1** — layer replication factor + session re-planning + resumable KV (hardest: per-stage KV is lost with the node; options: KV re-prefill from token history vs KV checkpointing) |
| **Dynamic membership** | topology fixed at session create | Critical | **Task 20.2** — join/leave protocol, background layer redistribution, planner re-run without killing sessions |
| **Extreme heterogeneity** | planner assumes 3 comparable nodes | High | Task 17.5 (extended: node classes §3, CPU-backend bandwidth calibration, per-node quant of KV) |
| **Multi-tenant serving / batching** | one stream, one session, queue depth 1–2 | High for economics | **Task 21** — continuous batching across sessions sharing a model (RFC-0013 wave model is batch-ready: a wave already carries `n_tokens`) |
| **WAN / non-LAN hops** | assumes LAN; clock-skew handling exists | Medium (LAN-first is fine for v1 network) | future — geographic stage clustering; speculation (Task 19) becomes mandatory, F amortization |
| **Trust / isolation** (corporate nodes running others' weights; users' prompts crossing volunteer nodes) | none | Medium now, critical for public network | future — not blocking homelab/company-internal deployments, which are the first users |
| **Incentives / accounting** | none | Out of scope for engineering roadmap | product decision |

## 6. Relationship to the existing roadmap (nothing is wasted)

- **Task 18.1/18.2 (lifecycle, sync)** — unchanged, rises further in priority: a network onboards nodes and models constantly.
- **Task 17.5 (planner)** — becomes the core product component; extended with node classes.
- **Task 17.1–17.4 (decode overhead)** — keeps small clusters and TTFT usable; F reduction matters more as hop counts grow.
- **Task 19 (speculative)** — the latency answer for many-node and WAN topologies (amortizes F ×k).
- **Capacity ladder (`TARGET_70B_GOAL_AND_FEASIBILITY.md` §6)** — remains the acceptance track on the reference 3-node cluster; the ladder extends upward (100B+, MoE) as the node pool grows.
- **New track: Task 20.x (elasticity), Task 21 (multi-tenant batching)** — to be specified after the 70B rung passes; fault tolerance design research can start earlier since it shapes protocol decisions (KV ownership, wave routing).

## 7. Prior art positioning (landscape as of mid-2026)

**Direct analogs (same idea — pool weak machines to run models that fit nowhere):**

| Project | Approach | Gap this project fills vs it |
|---------|----------|------------------------------|
| **prima.cpp** (ICLR 2026) | Technically closest: 30–70B on heterogeneous low-resource home clusters; piped-ring parallelism + mmap offload + speculative decoding; 70B ≈ 2 tok/s, 5–17× lower TPOT than exo/dllama | Runtime only — no control plane (registry, layer sync, coverage, planner-as-service, observability). Its scheduler and speculative results are direct inputs to Tasks 17.5 / 19 |
| **Exo** | P2P pool of one's own devices (Apple-centric), no master | Static membership, single-owner tool, no model lifecycle management |
| **Petals** | Internet-scale volunteer swarm, block replication, routing, churn tolerance | Seconds/token single-stream; research-grade. Replication/routing = blueprint for Task 20.x |
| **distributed-llama** | Tensor parallel over Ethernet | TP over 1 GbE ruled out by Study §13 latency floor |
| **llama.cpp RPC / SharedLLM / LocalAI federated** | Op-level or thin RPC federation over llama.cpp | Synchronous per-op or thin layer; no lifecycle, no planner |
| **Kalavai** | Volunteer device pooling platform (enterprise-leaning) | Aggregates devices for existing engines rather than owning a split runtime |

**Adjacent (decentralized inference economies):** Parallax/Gradient (decentralized LLM serving over heterogeneous GPUs, two-phase scheduler), Bittensor/Chutes, io.net, Akash — whole-GPU marketplaces with incentive layers, not layer-split across weak commodity nodes.

**This project's defensible slot:** a **private organization/community network** combining (a) weak-office-PC + strong-node heterogeneity as the primary input, (b) a full model-lifecycle control plane (registry → sync → coverage → planner → session → trace-verified metrics), (c) descriptor-driven support for any GGUF architecture without per-model code. No surveyed system combines all three.

**Action item:** add **prima.cpp as a competitive baseline** to the capacity ladder — run it on the same 3-node cluster at 30B/70B rungs (L2/L3) for an external reference point alongside internal ceiling percentages.

## 8. References

`docs/DISTRIBUTED_INFERENCE_PERFORMANCE_STUDY.md` §9–11, §13; `docs/TARGET_70B_GOAL_AND_FEASIBILITY.md`; `docs/archive/TASK_11_LAYER_FIRST_RUNTIME.md` (runtime roles); `docs/RFC_0013_DISTRIBUTED_RUNTIME_PROTOCOL_V2.md` (wave model).
