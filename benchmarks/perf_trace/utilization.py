#!/usr/bin/env python3
"""GPU / CPU utilization samples from Task 12 perf traces."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from perf_trace.merge import load_jsonl, write_csv


def load_gpu_events(trace_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not trace_dir.is_dir():
        return events
    for path in sorted(trace_dir.rglob("*.jsonl")):
        for ev in load_jsonl(path):
            if ev.get("event") == "GPU_SAMPLE":
                events.append(ev)
    return events


def _attrs(ev: dict[str, Any]) -> dict[str, Any]:
    raw = ev.get("attrs")
    return raw if isinstance(raw, dict) else {}


def gpu_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ev in events:
        attrs = _attrs(ev)
        rows.append({
            "trace_id": ev.get("trace_id", ""),
            "phase": ev.get("phase", ""),
            "node_id": ev.get("node_id", ""),
            "component": ev.get("component", ""),
            "backend": attrs.get("backend", "cpu"),
            "gpu_util_pct": attrs.get("gpu_util_pct"),
            "gpu_mem_used_mb": attrs.get("gpu_mem_used_mb"),
            "cpu_busy_pct": attrs.get("cpu_busy_pct"),
            "util_valid": attrs.get("util_valid"),
            "ts_us": ev.get("ts_us"),
        })
    return rows


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_node: dict[str, list[float]] = defaultdict(list)
    by_backend: dict[str, int] = defaultdict(int)
    by_phase: dict[str, list[float]] = defaultdict(list)
    util_valid_count = 0

    for ev in events:
        attrs = _attrs(ev)
        backend = str(attrs.get("backend", "cpu"))
        by_backend[backend] += 1
        util = attrs.get("gpu_util_pct")
        if attrs.get("util_valid") is True:
            util_valid_count += 1
        if isinstance(util, (int, float)):
            node = str(ev.get("node_id", "unknown"))
            phase = str(ev.get("phase", "unknown"))
            by_node[node].append(float(util))
            by_phase[phase].append(float(util))

    def stats(vals: list[float]) -> dict[str, float]:
        if not vals:
            return {}
        return {
            "count": len(vals),
            "avg": round(sum(vals) / len(vals), 2),
            "max": round(max(vals), 2),
            "min": round(min(vals), 2),
        }

    return {
        "sample_count": len(events),
        "util_valid_count": util_valid_count,
        "backends": dict(by_backend),
        "by_node": {k: stats(v) for k, v in sorted(by_node.items())},
        "by_phase": {k: stats(v) for k, v in sorted(by_phase.items())},
    }


def merge_utilization(trace_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    events = load_gpu_events(trace_dir)
    rows = gpu_rows(events)
    summary = summarize(events)
    document = {
        "trace_dir": str(trace_dir),
        "sample_count": len(events),
        "summary": summary,
        "samples": rows,
    }
    (out_dir / "gpu.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    write_csv(out_dir / "gpu.csv", rows)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge Task 12 GPU utilization samples")
    parser.add_argument("trace_dir", type=Path, help="Directory containing perf trace JSONL")
    parser.add_argument("-o", "--output", type=Path, help="Output directory")
    args = parser.parse_args()
    out = args.output or args.trace_dir
    doc = merge_utilization(args.trace_dir, out)
    print(json.dumps({
        "samples": doc["sample_count"],
        "summary": doc["summary"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
