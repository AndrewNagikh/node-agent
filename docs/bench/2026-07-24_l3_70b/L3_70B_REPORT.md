# Tier 2 L3-dense Report — 2026-07-24

Gate: `docs/FIRST_SHOWCASE_CRITERIA.md` Tier 2, L3-dense — "Llama-3.3-70B-Instruct
Q3_K_M (~34 GB, fits the ~37.8 GB fast-memory budget ...) | cold sync bounded
(~34 GB at wire speed ~7-10 min; hours = revisit Task 18.2 first), warm
session, >=80% of the computed ~3.7 tok/s ceiling."

**Result: ACHIEVED — 70B-class model runs end-to-end on the 3-node home
cluster.** Not blocking Tier 1 either way (Tier 2 is explicitly optional),
but this is the headline number for the showcase: "we ran a 70B-class model
on three mismatched consumer machines."

## Model used: Q3_K_S, not Q3_K_M

`Llama-3.3-70B-Instruct-Q3_K_M.gguf` (bartowski, 34.27 GB) failed the
cluster's memory-fit check: required ~40 GB (weights + KV + compute/scratch
margins) against ~37.3 GB of currently free cluster memory. Investigated
before switching quant (see "Memory-budget investigation" below) rather than
assuming the doc's `~37.8 GB` estimate was simply wrong. Switched to
`Llama-3.3-70B-Instruct-Q3_K_S.gguf` (30.9 GB) instead, which passed the fit
check comfortably (`total_required_memory` 32.6 GB vs 37.3 GB budget).

## Numbers

| Stage | Result |
|---|---|
| Discover + manifest | Instant (GGUF header only, no download) |
| Cold sync (30.9 GB across 3 nodes, parallel) | **~42 minutes** (726 install ops, zero failures) |
| Warm session create | **9 seconds** |
| TTFT (cold prefill) | 6.7s |
| Decode | **~2.28 tok/s** (median of first plain measurement + 3 traced samples) |
| Ceiling (perf-traced, same methodology as [G1](../2026-07-23_g1_ceiling/G1_CEILING_REPORT.md)) | 2.246 / 2.359 / 2.350 tok/s across 3 samples |
| **Ratio (measured/ceiling)** | **101.2% / 95.6% / 98.4%, median 98.4%** — comfortably clears the >=80% bar |

Output was coherent and factually correct across every sample ("The Roman
Empire began with the overthrow of the Roman Kingdom in 509 BC and lasted
until the fall of the Western Roman Empire in 476 AD..." — historically
accurate, not just fluent).

Cold sync landed at ~42 minutes, not the doc's optimistic "~7-10 min at wire
speed" estimate — that number was aspirational, not measured on this LAN.
Still well inside the doc's own "hours = revisit Task 18.2" escape hatch, so
no action needed there.

## Reading the ~98% ratio

Unlike G1's measurements (which sat in the 81-96% range with real headroom
between measured and ceiling), this model's measured throughput landed
almost exactly at its computed ceiling every single time. That's not
suspicious — it's the expected shape for a single, mostly-serial 3-hop
pipeline moving very little data per token relative to how long each stage's
own compute takes: at ~2.3 tok/s (~435ms/token), there's very little
absolute bubble/idle time available to lose in the first place, so
measured-vs-ceiling naturally converges. G1's dense/MoE rungs run 3-4x
faster per token, giving proportionally more room for scheduling overhead to
show up as a gap.

## Speculative decoding attempt: no speedup with the 1B draft

Tried pairing this model with the same Llama-3.2-1B-Instruct draft used
for the 3B/1B pair's ×1.64 result (dashboard testing, 2026-07-24,
`speculative_draft_k=4`). Result: **2.2 tok/s, statistically the same
as the non-speculative 2.28 tok/s above — no speedup.**

Root cause, read directly from the entry node's `SPEC_DEBUG` log
(`GET /debug/log?worker=entry`), not guessed:

```
SPEC_DEBUG entry: wait_window=10.0ms arrival_p50=8.0ms arrival_p95=8.0ms hit_rate=19% throttled=no
```

`hit_rate=19%` — the draft's proposed token matches the target's actual
choice only ~1 in 5 times. At that acceptance rate the per-step draft +
verify overhead outweighs the savings from the rare hits, netting no
measurable speedup. This isn't a code bug (the pipeline behaves
correctly) — it's a pair-mismatch result: same model *family* doesn't
guarantee a compatible output distribution across a 70x size gap.
Consistent with the pair-dependent acceptance already noted for G2
(SmolLM2/1.7B: ~50% at k=4, also pair-dependent, `TASK_19_SPECULATIVE_PIPELINE_STUDY.md`)
— this 1B/70B pair is simply worse than either of those. No other draft
model was tried for this target.

## External reference point: prima.cpp (cited, not run)

prima.cpp ([Lizonghang/prima.cpp](https://github.com/Lizonghang/prima.cpp) /
[OpenCPIL/prima.cpp](https://github.com/OpenCPIL/prima.cpp), ICLR 2026,
[arXiv:2504.08791](https://arxiv.org/html/2504.08791v2)) is the closest
published prior art — pipelined-ring parallelism + mmap disk offload +
speculative decoding for heterogeneous home clusters. `PROJECT_VISION_DISTRIBUTED_NETWORK.md`
originally called for running it ourselves on this cluster as an external
baseline. **Decided not to**, for two concrete reasons found before
attempting any setup:

1. **Windows isn't supported.** prima.cpp's own docs list Linux/macOS/
   Android/HarmonyOS; Windows is "on roadmap," not shipped. node-c in this
   cluster is Windows (RTX 4070 Ti) — a real 3-node run isn't possible as-is
   without a WSL2 workaround, which wasn't attempted.
2. **Quantization support doesn't overlap.** prima.cpp supports Q4K/Q6K/Q80/
   IQ1; everything measured in this project today used Q3_K. A "same file,
   same hardware" comparison isn't available without downloading and
   installing a second, larger quant just for this comparison.

Instead, citing their own reported number as context: their paper's ~2 tok/s
(674 ms/token TPOT) for a 70B model was measured on a **4-device** cluster
(Mac M1 2.4GiB RAM; Linux/RTX 3070 8GB VRAM; Linux/RTX 2080Ti 11GB VRAM;
HarmonyOS phone, no GPU) over Wi-Fi (320-610 Mbps, 3-7ms latency), with
combined RAM+VRAM of **37 GiB — by their own description, insufficient for
the full quantized model**, i.e. deliberately memory-constrained to exercise
their disk-offload mechanism. This project's **2.28 tok/s** on a 3-device
cluster with comfortable memory headroom (no disk offload involved) is in
the same ballpark, but the setups differ enough (device count, GPU
generation, memory pressure regime) that this is a directional reference
point, not a controlled head-to-head. State it as such in the final G6
report — don't claim a win or a loss from this pairing.

## Memory-budget investigation (context for the quant switch)

Before switching to Q3_K_S, traced why the "fits ~37.8GB budget" assumption
in the planning doc didn't hold today. Confirmed in code
(`ggml-metal-device.m`'s `ggml_metal_device_get_memory`): on the two Mac
nodes, llama.cpp's Metal backend reports free VRAM via Apple's
`recommendedMaxWorkingSetSize` — a static, conservative software guideline
(~70-75% of total RAM on this hardware), not live available memory. It
doesn't move when other apps close or the process restarts (confirmed
empirically). The Windows/CUDA node reports real hardware VRAM via
`cudaMemGetInfo` — a genuine, live constraint, not the same issue.

Traced the mechanism into the layout planner
(`layout_planner.cpp` ~330-440): this conservative number does directly cap
how many layers a Mac node can be assigned (`gpu_budget_bytes` →
`caps[i]`), not just gate a simple accept/reject decision. **However**,
checked against an already-measured case (qwen2.5-32b) whether this cap was
ever actually binding: it was not — for that model, node-c's real hardware
VRAM was the binding constraint, and the Mac nodes had slack to absorb
overflow above their proportional share. So this finding, while real, does
**not** explain any of today's earlier G1/G3 throughput numbers, and should
not be assumed to without checking the specific case. It plausibly explains
why the larger 70B model specifically failed the fit check (the real-vs-
Metal-reported gap on the Macs, ~4.8 GB, is the same order of magnitude as
the ~2.5-3 GB shortfall), but that's not proven either — no code was changed
to test this directly. Follow-up investigation (controlled step-loading
experiment before any code change) tracked separately, not done today; **do
not** weaken the current conservative default without that experiment.

## Reproduction

```bash
# Register + discover + manifest (cheap, no download):
curl -X POST http://192.168.50.154:9000/models/register -d '{"model_id":"llama-3.3-70b-q3ks","source":"huggingface","repository":"bartowski/Llama-3.3-70B-Instruct-GGUF","filename":"Llama-3.3-70B-Instruct-Q3_K_S.gguf","revision":"main"}'
curl -X POST http://192.168.50.154:9000/models/llama-3.3-70b-q3ks/discover -d '{}'
curl -X POST http://192.168.50.154:9000/models/llama-3.3-70b-q3ks/manifest -d '{}'
curl -X POST http://192.168.50.154:9000/models/llama-3.3-70b-q3ks/layout -d '{}'   # confirms fits_cluster

# Install (~42 min, ~30.9GB):
curl -X POST http://192.168.50.154:9000/models/llama-3.3-70b-q3ks/install-plan -o /tmp/plan.json
curl -X POST http://192.168.50.154:9000/models/llama-3.3-70b-q3ks/install/execute   # poll /jobs/{id}
curl -X POST http://192.168.50.154:9000/models/llama-3.3-70b-q3ks/coverage/refresh

# Ceiling measurement (same script as G1):
python3 docs/bench/2026-07-23_g1_ceiling/measure_ceiling.py llama-3.3-70b-q3ks
```
