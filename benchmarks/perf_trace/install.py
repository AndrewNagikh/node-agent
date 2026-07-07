#!/usr/bin/env python3
"""Merge install perf trace JSONL into install.json / install_reuse.json."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from perf_trace.merge import load_jsonl, load_trace_dir, _ms


def is_install_event(ev: dict[str, Any]) -> bool:
    phase = str(ev.get("phase", ""))
    if phase == "install":
        return True
    event = str(ev.get("event", ""))
    return event.startswith("INSTALL_")


def load_install_events(trace_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not trace_dir.is_dir():
        return events
    for path in sorted(trace_dir.rglob("*.jsonl")):
        for ev in load_jsonl(path):
            if is_install_event(ev):
                events.append(ev)
    return events


def install_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") != "span":
            continue
        attrs = ev.get("attrs") or {}
        if not isinstance(attrs, dict):
            attrs = {}
        rows.append({
            "trace_id": ev.get("trace_id", ""),
            "event": ev.get("event", ""),
            "sub": attrs.get("sub", ""),
            "blob_id": attrs.get("blob_id", ""),
            "node_id": ev.get("node_id", ""),
            "bytes": attrs.get("bytes", 0),
            "dur_ms": _ms(ev.get("dur_us")),
            "ts_us": ev.get("ts_us"),
        })
    return rows


def reuse_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_sub: dict[str, int] = defaultdict(int)
    bytes_by_sub: dict[str, int] = defaultdict(int)
    dur_by_sub: dict[str, float] = defaultdict(float)

    for ev in events:
        attrs = ev.get("attrs") or {}
        if not isinstance(attrs, dict):
            continue
        sub = str(attrs.get("sub", "unknown"))
        by_sub[sub] += 1
        bytes_by_sub[sub] += int(attrs.get("bytes", 0) or 0)
        dur = ev.get("dur_us")
        if isinstance(dur, (int, float)):
            dur_by_sub[sub] += float(dur)

    total_ops = sum(by_sub.values()) or 1
    reuse_ops = by_sub.get("reuse", 0) + by_sub.get("cache_hit", 0)
    download_ops = by_sub.get("download", 0) + by_sub.get("repair", 0)
    pct = {k: round(100.0 * v / total_ops, 2) for k, v in sorted(by_sub.items())}

    return {
        "operation_counts": dict(by_sub),
        "bytes_by_sub": dict(bytes_by_sub),
        "dur_us_by_sub": {k: int(v) for k, v in dur_by_sub.items()},
        "reuse_pct": round(100.0 * reuse_ops / total_ops, 2),
        "download_pct": round(100.0 * download_ops / total_ops, 2),
        "sub_pct": pct,
        "full_reuse": any(
            ev.get("event") == "INSTALL_FULL_REUSE" for ev in events if ev.get("kind") == "instant"
        ),
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


def merge_install_trace(trace_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    out = out_dir or (trace_dir / "install_analysis")
    out.mkdir(parents=True, exist_ok=True)

    events = load_install_events(trace_dir)
    rows = install_rows(events)
    reuse = reuse_summary(events)

    document = {
        "trace_dir": str(trace_dir),
        "event_count": len(events),
        "blob_operations": len(rows),
        "reuse": reuse,
        "events": events,
        "rows": rows,
    }
    (out / "install.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    write_csv(out / "install.csv", rows)
    (out / "install_reuse.json").write_text(json.dumps(reuse, indent=2), encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge Task 12 install perf trace")
    parser.add_argument("trace_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    doc = merge_install_trace(args.trace_dir, args.output)
    print(json.dumps({
        "events": doc["event_count"],
        "blob_operations": doc["blob_operations"],
        "reuse": doc["reuse"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
