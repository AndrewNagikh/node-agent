#!/usr/bin/env python3
"""GGML / scheduler sub-span summary for Task 12.7 traces."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from perf_trace.merge import load_jsonl, _ms

GGML_EVENTS = {
    "GGML_GRAPH_BUILD",
    "GGML_GRAPH_EXECUTE",
    "GGML_BACKEND_SYNC",
    "SCHED_QUEUE_WAIT",
    "SCHED_MUTEX_WAIT",
    "SCHED_CV_WAIT",
}


def load_ggml_events(trace_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not trace_dir.is_dir():
        return events
    for path in sorted(trace_dir.rglob("*.jsonl")):
        for ev in load_jsonl(path):
            if ev.get("kind") == "span" and ev.get("event") in GGML_EVENTS:
                events.append(ev)
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_event: dict[str, float] = defaultdict(float)
    by_stage: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)

    for ev in events:
        event = str(ev.get("event", ""))
        dur = ev.get("dur_us")
        if not isinstance(dur, (int, float)):
            continue
        by_event[event] += float(dur)
        by_stage[str(ev.get("stage", "unknown"))] += float(dur)
        counts[event] += 1

    total = sum(by_event.values()) or 1.0
    return {
        "span_count": len(events),
        "event_counts": dict(counts),
        "event_us": {k: int(v) for k, v in by_event.items()},
        "event_pct": {k: round(100.0 * v / total, 2) for k, v in sorted(by_event.items(), key=lambda x: -x[1])},
        "stage_us": {k: int(v) for k, v in by_stage.items()},
        "event_ms_avg": {
            k: round(_ms(by_event[k] / counts[k]) or 0.0, 3)
            for k in counts
            if counts[k] > 0
        },
    }


def merge_ggml(trace_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    events = load_ggml_events(trace_dir)
    summary = summarize(events)
    document = {
        "trace_dir": str(trace_dir),
        "span_count": len(events),
        "summary": summary,
        "events": events,
    }
    (out_dir / "ggml.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge Task 12 GGML perf spans")
    parser.add_argument("trace_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    out = args.output or args.trace_dir
    doc = merge_ggml(args.trace_dir, out)
    print(json.dumps({"spans": doc["span_count"], "summary": doc["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
