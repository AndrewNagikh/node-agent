#!/usr/bin/env python3
"""Merge TTFT perf trace JSONL into ttft.json / ttft.csv."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from perf_trace.merge import load_jsonl, _ms

TTFT_MARKERS = (
    "TTFT_",
    "CLIENT_TTFT",
    "CLIENT_RESPONSE",
)


def is_ttft_event(ev: dict[str, Any]) -> bool:
    if str(ev.get("phase", "")) == "ttft":
        return True
    event = str(ev.get("event", ""))
    return any(event.startswith(m) or event == m for m in TTFT_MARKERS)


def load_ttft_events(trace_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not trace_dir.is_dir():
        return events
    for path in sorted(trace_dir.rglob("*.jsonl")):
        for ev in load_jsonl(path):
            if is_ttft_event(ev):
                events.append(ev)
    return events


def ttft_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") != "span":
            continue
        attrs = ev.get("attrs") if isinstance(ev.get("attrs"), dict) else {}
        rows.append({
            "trace_id": ev.get("trace_id", ""),
            "event": ev.get("event", ""),
            "stage": ev.get("stage", ""),
            "node_id": ev.get("node_id", ""),
            "category": ev.get("category", ""),
            "dur_ms": _ms(ev.get("dur_us")),
            "token_id": attrs.get("token_id"),
            "ts_us": ev.get("ts_us"),
        })
    return rows


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_event: dict[str, float] = defaultdict(float)
    by_stage: dict[str, float] = defaultdict(float)
    client_ttft_ms: float | None = None

    for ev in events:
        event = str(ev.get("event", ""))
        if event == "CLIENT_TTFT" and ev.get("kind") == "instant":
            attrs = ev.get("attrs") if isinstance(ev.get("attrs"), dict) else {}
            prefill_ms = attrs.get("prefill_ms")
            if isinstance(prefill_ms, (int, float)):
                client_ttft_ms = float(prefill_ms)
        if ev.get("kind") != "span":
            continue
        dur = ev.get("dur_us")
        if not isinstance(dur, (int, float)):
            continue
        by_event[event] += float(dur)
        by_stage[str(ev.get("stage", "unknown"))] += float(dur)

    total = sum(by_event.values()) or 1.0
    return {
        "client_ttft_ms": client_ttft_ms,
        "event_us": {k: int(v) for k, v in sorted(by_event.items(), key=lambda x: -x[1])},
        "event_pct": {k: round(100.0 * v / total, 2) for k, v in sorted(by_event.items(), key=lambda x: -x[1])},
        "stage_us": {k: int(v) for k, v in sorted(by_stage.items(), key=lambda x: -x[1])},
        "prefill_wall_ms": round(total / 1000.0, 3),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def merge_ttft_trace(trace_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    out = out_dir or (trace_dir / "ttft_analysis")
    out.mkdir(parents=True, exist_ok=True)
    events = load_ttft_events(trace_dir)
    rows = ttft_rows(events)
    summary = summarize(events)
    document = {
        "trace_dir": str(trace_dir),
        "event_count": len(events),
        "span_count": len(rows),
        "summary": summary,
        "events": events,
        "rows": rows,
    }
    (out / "ttft.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    write_csv(out / "ttft.csv", rows)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge Task 12 TTFT perf trace")
    parser.add_argument("trace_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    doc = merge_ttft_trace(args.trace_dir, args.output)
    print(json.dumps({
        "events": doc["event_count"],
        "spans": doc["span_count"],
        "summary": doc["summary"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
