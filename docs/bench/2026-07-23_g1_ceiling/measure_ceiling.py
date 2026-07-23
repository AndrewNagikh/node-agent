#!/usr/bin/env python3
"""Measure G1's ceiling % for one model: perf-traced session create + 64-token
generate + destroy, collect the trace, compute the ceiling from real decode
spans (never hand-estimated, per docs/PERFORMANCE_METRICS_SPEC.md), and
report measured/ceiling from the SAME traced run (the only apples-to-apples
comparison -- mixing an untraced measured_tps against a traced-derived
ceiling is invalid, see G1_CEILING_REPORT.md).

Usage:
    python3 docs/bench/2026-07-23_g1_ceiling/measure_ceiling.py qwen2.5-32b
    python3 docs/bench/2026-07-23_g1_ceiling/measure_ceiling.py qwen3-30b
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "benchmarks"))

ORCHESTRATOR = os.environ.get("ORCHESTRATOR", "http://192.168.50.154:9000")
PROMPT = "The history of the Roman Empire began"
MAX_TOKENS = 64


def http_post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{ORCHESTRATOR}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    model = sys.argv[1]

    os.environ["BENCHMARK_DOCKER"] = "0"
    from perf_trace.collect import collect_traces
    from perf_trace.metric_validation import (
        compute_critical_path_tokens,
        filter_trace,
        load_raw_events,
    )

    print(f"creating perf-traced session for {model}...")
    create = http_post("/session/create", {"model": model, "perf_trace": True})
    if create.get("error"):
        print("session/create failed:", create["error"])
        return 1
    session_id = create["session_id"]

    print("generating...")
    gen = http_post(
        "/session/generate",
        {"session_id": session_id, "prompt": PROMPT, "max_tokens": MAX_TOKENS},
    )
    timing = gen.get("timing", {})
    trace_id = timing.get("trace_id")
    measured_tps = timing.get("decode_tokens_per_sec")
    print(f"measured (this traced run): {measured_tps:.3f} tok/s, trace_id={trace_id}")

    http_post("/session/destroy", {"session_id": session_id})

    with tempfile.TemporaryDirectory() as tmp:
        cluster = json.loads(urllib.request.urlopen(f"{ORCHESTRATOR}/nodes").read())
        n = collect_traces(Path(tmp), orchestrator=ORCHESTRATOR, cluster=cluster)
        print(f"collected {n} trace files")

        events = load_raw_events(Path(tmp))
        sub = filter_trace(events, trace_id)
        result = compute_critical_path_tokens(sub, phase="decode")

    avg_ms = result.get("avg_effective_critical_path_ms")
    if not avg_ms:
        print("could not compute critical path -- see full result:")
        print(json.dumps({k: v for k, v in result.items() if k != "token_rows"}, indent=2))
        return 1

    ceiling_tps = 1000.0 / avg_ms
    ratio = measured_tps / ceiling_tps
    print()
    print(f"ceiling:  {ceiling_tps:.3f} tok/s  (avg_effective_critical_path_ms={avg_ms:.1f}, "
          f"source={result['effective_path_source']}, clock_skew_wave_count={result['clock_skew_wave_count']})")
    print(f"measured: {measured_tps:.3f} tok/s")
    print(f"ratio:    {ratio*100:.1f}%  ({'PASS' if ratio >= 0.8 else 'FAIL'} G1's >=80% threshold)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
