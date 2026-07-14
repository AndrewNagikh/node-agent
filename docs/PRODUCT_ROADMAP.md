# Product Roadmap — from working runtime to community launch

**Goal (owner-stated, 2026-07-15):** community traction for home/LAN use; money is not the primary objective. The public compute-sharing network idea is explicitly deferred — LAN value exists at N=1 household and needs no network effect.

## The demo is the product gate

Nobody looks at a distributed-inference demo unless it shows **stably high t/s on a model that no single node can run**. Two consequences:

1. **Demo model class: MoE ~30B, not dense 70B.** Back-of-envelope on the current cluster (fast memory ≈ 35 GB: M3 Pro 13.3 @150 GB/s, M1 Pro 10.7 @200 GB/s, RTX 4070 Ti 10.8 @504 GB/s):
   - Dense 70B (IQ3/Q3, 27–34 GB): fit-constrained placement forces most bytes onto the slow Macs → period ≈ 76 ms compute + transport → **ceiling ~10–11 tok/s, realistic 7–9**. Watchable, not viral, and IQ3 quality is marginal.
   - MoE ~30B-A3B class (e.g. Qwen3-30B-A3B, Q4 ≈ 18.6 GB): fits **no single node** (max is 13.3 GB) so the pitch is honest, but active ~3B params → ~2 GB read/token spread over three nodes ≈ 8–10 ms compute + sampler + transport → **realistic 30–40 tok/s**.
   - Numbers are estimates; validate fit + predicted TPS with the memory estimator/planner before committing.
2. **"Stably" matters as much as "high".** Measured jitter (median 30.9 ms but p95 89 / max 113 ms) is exactly what makes a demo look broken. Steady 30 beats bursty 40. The fix is 17.1 Phase B (true pipelining of ack/COMPLETE), so the perf plan and the demo plan converge.

## Order of work

1. **Perf/stability minimum** (current Task 17 continuation, see SESSION_2026-07-15 doc):
   tooling fixes → interim planner fix (real bandwidth-based node score + final role to strongest node; expected ~6–7 ms/token from moving final+sampler off the M1 Pro) → 17.2B sampler path → 17.1 Phase B (jitter/residual).
2. **Demo milestone:** MoE ~30B running on the 3-node homelab at stable 30+ tok/s, with honest recorded numbers.
3. **UX minimum — deliberately NOT a desktop app first:**
   1. **OpenAI-compatible API** (`/v1/chat/completions`) on the orchestrator — unlocks every existing client (Open WebUI, Continue, Raycast, …) for free; highest leverage per line of code in the project.
   2. **Web dashboard served by the orchestrator** (it is already an HTTP server): node status, layer coverage, install progress, log tails (endpoints exist: `/debug/log`), tiny chat playground.
   3. **mDNS auto-discovery** replacing `nodes.conf` + one install script per platform ("run one command on each machine, they find each other").
   4. Desktop app (Tauri wrapping the same dashboard) — last, cosmetic.
4. **Launch:** README with honest benchmarks + short video; post to r/LocalLLaMA (primary audience) and HN.

## Positioning notes

- Distributed always loses to local when the model fits one machine — never pitch small-model TPS. The product exists only for models that don't fit any single node.
- Differentiator vs exo/llama.cpp-RPC/Petals: **heterogeneous mix** (Metal + CUDA + Windows/WSL) with recovery and honest perf telemetry; MoE-aware placement is a future unique edge (MoE favors distribution: huge memory, small active compute).
- Main strategic risk is not competitors but single-node hardware growth (large unified-memory Macs) eating the niche from below → target the MoE-large segment where the niche persists.
