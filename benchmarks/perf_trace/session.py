#!/usr/bin/env python3
"""Merge session_create perf trace JSONL into session.json."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from perf_trace.merge import load_jsonl, _ms

SESSION_EVENTS = {
    "SESSION_BEGIN",
    "SESSION_END",
    "SESSION_RESOLVE_LAYOUT",
    "SESSION_COVERAGE_CHECK",
    "SESSION_SHUTDOWN_NODES",
    "SESSION_PREPARE_RUNTIME",
    "SESSION_CONFIGURE_NODE",
    "SESSION_WORKER_STARTUP",
    "SESSION_READY_WAIT",
    "SESSION_SERVICE_CONFIGURE",
}


def is_session_event(ev: dict[str, Any]) -> bool:
    if str(ev.get("phase", "")) == "session_create":
        return True
    return str(ev.get("event", "")) in SESSION_EVENTS


def load_session_events(trace_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not trace_dir.is_dir():
        return events
    for path in sorted(trace_dir.rglob("*.jsonl")):
        for ev in load_jsonl(path):
            if is_session_event(ev):
                events.append(ev)
    return events


def span_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") != "span":
            continue
        attrs = ev.get("attrs") if isinstance(ev.get("attrs"), dict) else {}
        rows.append({
            "trace_id": ev.get("trace_id", ""),
            "event": ev.get("event", ""),
            "node_id": ev.get("node_id", ""),
            "role": attrs.get("role", ev.get("stage", "")),
            "dur_ms": _ms(ev.get("dur_us")),
            "ts_us": ev.get("ts_us"),
        })
    return rows


def breakdown(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_event: dict[str, float] = defaultdict(float)
    by_node: dict[str, float] = defaultdict(float)
    for ev in events:
        if ev.get("kind") != "span":
            continue
        event = str(ev.get("event", "UNKNOWN"))
        dur = ev.get("dur_us")
        if not isinstance(dur, (int, float)):
            continue
        by_event[event] += float(dur)
        node_id = str(ev.get("node_id", "unknown"))
        by_node[node_id] += float(dur)
    total = sum(by_event.values()) or 1.0
    return {
        "event_us": {k: int(v) for k, v in sorted(by_event.items(), key=lambda x: -x[1])},
        "event_pct": {k: round(100.0 * v / total, 2) for k, v in sorted(by_event.items(), key=lambda x: -x[1])},
        "node_us": {k: int(v) for k, v in sorted(by_node.items(), key=lambda x: -x[1])},
        "total_ms": round(total / 1000.0, 3),
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


def merge_session_trace(trace_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    out = out_dir or (trace_dir / "session_analysis")
    out.mkdir(parents=True, exist_ok=True)
    events = load_session_events(trace_dir)
    rows = span_rows(events)
    summary = breakdown(events)
    document = {
        "trace_dir": str(trace_dir),
        "event_count": len(events),
        "span_count": len(rows),
        "breakdown": summary,
        "events": events,
        "spans": rows,
    }
    (out / "session.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    write_csv(out / "session.csv", rows)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge Task 12 session_create perf trace")
    parser.add_argument("trace_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    doc = merge_session_trace(args.trace_dir, args.output)
    print(json.dumps({
        "events": doc["event_count"],
        "spans": doc["span_count"],
        "breakdown": doc["breakdown"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
