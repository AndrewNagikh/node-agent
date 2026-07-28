# distributed-llm

**Several mismatched consumer machines — two laptops and a gaming PC — working
as one, running models none of them could run alone, with one command per node
and honest numbers.**

This is a distributed LLM inference runtime for heterogeneous home hardware. It
splits a model's layers across the machines you already own and runs them as a
single pipeline. A 70B-class model runs on three consumer machines that share
no OS, no GPU vendor, and no network medium.

The goal is not to beat a single powerful GPU. It is to make several ordinary
machines behave as one inference runtime — and to say plainly how well that
works.

---

## The cluster these numbers come from

| Node | Machine | Backend | Link |
|---|---|---|---|
| node-a | Mac, M3 Pro | Metal | Ethernet |
| node-b | Mac, M1 Pro | Metal | **Wi-Fi** (weakest link) |
| node-c | Windows, RTX 4070 Ti | CUDA | Ethernet |

No two nodes share an operating system, a GPU vendor, or a network medium.
Every number below was measured on this exact cluster — nothing simulated,
nothing extrapolated from a single-node figure.

## Measured results

Fresh measurements, 2026-07-27, 64-token generations on an idle cluster:

| Model | Params | Decode | TTFT | Session create | Pipeline |
|---|---|---|---|---|---|
| llama-3.2-3b | 3B | **23.1** tok/s | 146 ms | 4.2 s | 3 nodes |
| qwen3-14b | 14B | **12.0** tok/s | 290 ms | 5.8 s | 3 nodes |
| qwen3-30b | 30B MoE | **20.2** tok/s | 156 ms | 7.9 s | 3 nodes |
| qwen2.5-32b | 32B dense | **7.2** tok/s | 541 ms | 17.0 s | 3 nodes |
| Llama-3.3-70B-Instruct Q3_K_S | 70B dense | **2.4** tok/s | 2573 ms | 9.6 s | 3 nodes |

Median of 3 runs (2 for 70B). These are small samples taken back-to-back — the
larger-sample figures below come from a 30-minute soak with 47–48 cycles per
model, and should be trusted more for the smaller rungs.

**The point of the table**: 18.5 GB of weights (32B) and 30.9 GB (70B) fit in
no single node's fast memory on this cluster. They run anyway.

### Efficiency against the computed ceiling

The honest question is not "how fast is it" but "how much of what this hardware
could theoretically deliver are we actually getting". Measured from
perf-traced runs, comparing measured against the ceiling computed from real
per-stage spans in the *same* run:

| Model | % of computed ceiling |
|---|---|
| qwen2.5-32b | **86.9%** median (81.2–89.6%, n=3) |
| qwen3-30b | **86.7%** median (60.1–88.5%, n=3) |
| Llama-3.3-70B Q3_K_S | **98.4%** median (95.6–101.2%, n=3) |

Small, fast models score much lower (3B: 41%, 14B: 63%) — that is a limitation
of the measurement instrument, not the runtime. Tracing has a fixed per-token
cost that is a rounding error at 440 ms/token and a large fraction at 35 ms/token.
The production numbers in the first table are untraced and unaffected.

### Reliability

**190/190 create/generate/destroy cycles, 100% success, zero crashes, zero
corrupted output** over a 30-minute soak across 4 rotating models. 387 cycles
across all three soak runs. This is not a fault-tolerance claim — no node
failures were injected — it is "doesn't fall over under normal repeated use".

### Speculative decoding

Default-on, no flags. **×1.64 median** speedup on the Llama-3.2 3B/1B pair
(baseline 22.5–27.3 → speculative 40.8–45.7 tok/s).

Acceptance is strongly pair-dependent and we say so: the same 1B draft against
Llama-3.3-70B gave a **19% hit rate and no speedup at all**. A plausible-looking
draft pair is a hypothesis to measure, not a promise.

Full methodology, caveats and reproduction steps:
**[docs/HONEST_BENCHMARK_REPORT.md](docs/HONEST_BENCHMARK_REPORT.md)**

---

## How it works

```
        ┌─────────┐      ┌─────────┐      ┌─────────┐
client →│  entry  │ ───→ │ middle  │ ───→ │  final  │→ token
        │ layers  │      │ layers  │      │ layers  │
        │  0..4   │      │  5..9   │      │ 10..27  │
        └─────────┘      └─────────┘      └─────────┘
         node-b           node-a           node-c
```

- **Layer sharding.** The orchestrator reads the GGUF header, computes what the
  model needs (weights + KV + compute buffers), and assigns each node a
  contiguous layer range that fits its free memory.
- **Hidden states cross the wire**, not weights. Per token, per hop, that is a
  few KB — which is why this works over home Wi-Fi at all.
- **KV cache is layer-local.** Each node allocates cache only for the layers it
  owns. (Until recently every node allocated for the whole model — three full
  copies on a three-node split. That replication, not total memory, was what
  capped usable context.)
- **Speculative decoding** runs a small draft model on the final node and
  verifies whole waves at once, with a direct entry↔final link so draft
  delivery doesn't wait on the chain.
- **Content-addressed layer store.** Models are fetched once, sliced per node,
  and verified by checksum; a node only downloads the layers it was assigned.

## Quick start

```bash
git clone --recurse-submodules git@github.com:AndrewNagikh/node-agent.git
cd node-agent
git submodule update --init --recursive

./build.sh          # auto: Metal on Mac, CUDA if nvidia-smi, else CPU
./build.sh all      # + orchestrator
```

Tell the nodes where the orchestrator is. That is the only address anyone has
to type — each machine detects its own and advertises it when it registers, so
the orchestrator learns the rest of the topology by itself.

```bash
export ORCHESTRATOR=http://192.0.2.10:9000
```

Or put it in a config file instead, if you prefer it persisted:

```bash
cp nodes.conf.example nodes.conf
$EDITOR nodes.conf   # ORCHESTRATOR_HOST -- the other keys are optional overrides
```

The dashboard has a field for it too, asked once and remembered.

Then, one command per machine:

```bash
./run-orchestrator.sh          # on the coordinating machine
./run-agent.sh NODE_ID=node-a  # on each Mac / Linux node
.\run-agent.ps1 NodeId=node-c  # on Windows (see docs/WINDOWS_NODE_C_HANDOFF.md)
```

`run-agent.sh` auto-detects LAN IP, model path, GPU backend and benchmark score.
Override with `MODEL=/path/to/model.gguf`, `ADVERTISE_HOST=...`, or
`REBENCHMARK=1` to re-score the node.

## Using it

### Desktop dashboard

```bash
cd dashboard-app && npm install && npm run dev
```

Launching the app **is** this machine's node — it spawns the local agent, shows
cluster state, and can update and restart it. Browse and install models from
Hugging Face with a fit estimate against your cluster, chat with per-session
sampling settings, and repair a model whose layers drifted out of place.

### HTTP API

```bash
curl -s http://ORCHESTRATOR:9000/session/create \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama-3.2-3b"}'

curl -s http://ORCHESTRATOR:9000/session/generate \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"sess-...",
       "messages":[{"role":"user","content":"Привет"}],
       "max_tokens":128, "chat":true}'
```

### OpenAI-compatible endpoint

Point any OpenAI client at the orchestrator:

```bash
curl -s http://ORCHESTRATOR:9000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama-3.2-3b",
       "messages":[{"role":"user","content":"Hello"}],
       "max_tokens":64}'
```

`GET /v1/models` lists what the cluster can serve. Sessions are cached and
reused per model + sampling settings, so a request doesn't pay session setup
every time. Streaming is not implemented yet and is refused explicitly rather
than answered with a body a streaming client cannot parse.

## Supported architectures

| Architecture | Status | Partial forward | Hidden injection | Layer-first | Generate |
|---|---|---|---|---|---|
| Llama | ✅ | ✅ | ✅ | ✅ | ✅ |
| Qwen (incl. MoE) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Gemma | ✅ | ✅ | ✅ | ✅ | ✅ |
| Phi | 🟡 | ✅ | ✅ | ✅ | sync |
| SmolLM | 🟡 | ✅ | ✅ | ✅ | sync |
| DeepSeek-Qwen | 🟡 | via Qwen | via Qwen | ✅ | sync |

27 MoE architectures carry the layer-range support needed for partial forward.
Details: [docs/supported_architectures.md](docs/supported_architectures.md).

## Limitations

Stated plainly, because a benchmark without them is marketing:

- **Single stream.** No multi-tenant batching. Concurrent requests serialize.
- **No fault tolerance.** A node dropping mid-session is not handled.
- **LAN only.** Not designed or tested for WAN.
- **Long-prompt prefill is slow on big models.** TTFT scales from 146 ms at 3B
  to ~2.6 s at 70B; long-context prefill was not separately benchmarked.
- **Metal reports memory conservatively.** Free VRAM on the Mac nodes comes from
  Apple's `recommendedMaxWorkingSetSize`, a static guideline rather than live
  availability — so fit checks there are pessimistic.
- **Cold sync is slow.** ~42 minutes for the 70B's 30.9 GB across three nodes.

## Docs

- [Honest benchmark report](docs/HONEST_BENCHMARK_REPORT.md) — every number, with method and caveats
- [Known issues](docs/KNOWN_ISSUES.md) — open bugs and design reversals, including why some ideas were reverted
- [Showcase criteria](docs/FIRST_SHOWCASE_CRITERIA.md) — the gates this project held itself to
- [Supported architectures](docs/supported_architectures.md)
- [Windows node setup](docs/WINDOWS_NODE_C_HANDOFF.md)

## Reproducing the numbers

```bash
# Ceiling % for any model (perf-traced session + 64-token generate)
python3 docs/bench/2026-07-23_g1_ceiling/measure_ceiling.py qwen2.5-32b

# Reliability soak
docs/bench/2026-07-23_g3_soak/soak_test_v3.sh
```
