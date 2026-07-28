# Task 20 Phase 0 — offline top-k draft acceptance

Answers the one question Phase 0 exists to answer: how much more often does
the target's next token appear in a *set* of draft candidates than in the
draft's single best guess. Everything else about tree speculation depends on
that number, and it costs nothing to measure — no cluster, no distributed
code, two models on one machine.

**Result and decision: [PHASE0_REPORT.md](PHASE0_REPORT.md). Outcome is
NO-GO for Task 20.**

## What it does

Runs the target greedily to produce a reference continuation. At every step
it asks the draft, from the identical context, for its top-4 candidates, and
records where the target's actual next token landed — rank 0 (what linear
speculation accepts today), rank 1, rank 2, or nowhere.

Both models are fed the target's token at every step, so the draft is never
judged on a context the target did not actually produce. Greedy on both
sides means no sampling noise: the same pair of files gives the same
numbers on every run.

## Running it

Needs two full GGUF models with the **same vocabulary** — the tool refuses
the pair otherwise, since candidate token ids from a different tokenizer are
not comparable (and such a pair cannot be used for speculation at all).

```bash
curl -L -o /tmp/draft-1b.gguf https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf
```

```bash
curl -L -o /tmp/target-3b.gguf https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf
```

Build against the already-built llama.cpp in this repo (no CMake changes —
this is a one-off measurement tool, not part of the product):

```bash
cd llama.cpp && c++ -std=c++17 -O2 -I include -I ggml/include ../docs/bench/2026-07-27_tree_spec_phase0/measure_topk_acceptance.cpp -L build/bin -lllama -lggml -lggml-base -Wl,-rpath,$(pwd)/build/bin -o /tmp/measure_topk
```

```bash
/tmp/measure_topk /tmp/target-3b.gguf /tmp/draft-1b.gguf
```

Takes a couple of minutes on an M-series laptop. The `.gguf` files are not
committed — 2.7 GB, and re-downloading is cheaper than storing them.

## Reading the output

Per-prompt lines come first so a single talkative prompt can't hide behind
the average. Then the aggregate, then `E` — expected accepted tokens per
verify wave, `1 + sum p^i` over the draft depth — which is what the plan's
decision table is actually about, not the raw per-step acceptance rate. The
last block converts each decision band into a ceiling on verify cost: the
number a prototype would have to come in under.
