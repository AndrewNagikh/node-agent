#!/usr/bin/env python3
"""Queue depth extraction and stats for decode perf traces."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from perf_trace.merge import load_jsonl, write_csv

STAGES = ("entry", "middle", "final")


def queue_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_token: dict[int, dict[str, Any]] = defaultdict(dict)
    for ev in events:
        if ev.get("phase") != "decode" or ev.get("event") != "QUEUE_DEPTH":
            continue
        tok = int(ev.get("token_idx", -1))
        if tok < 0:
            continue
        stage = str(ev.get("stage", ""))
        if stage not in STAGES:
            continue
        attrs = ev.get("attrs") if isinstance(ev.get("attrs"), dict) else {}
        depth = attrs.get("depth")
        row = by_token[tok]
        row["token"] = tok
        row.setdefault("trace_id", ev.get("trace_id", ""))
        row[f"{stage}_queue_depth"] = depth
    return [by_token[tok] for tok in sorted(by_token)]


def queue_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for stage in STAGES:
        key = f"{stage}_queue_depth"
        vals = [int(r[key]) for r in rows if key in r and r[key] is not None]
        if not vals:
            continue
        summary[stage] = {
            "count": len(vals),
            "max": max(vals),
            "avg": round(sum(vals) / len(vals), 3),
            "pattern": ",".join(str(v) for v in vals),
        }
    return summary


def merge_queue(events: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = queue_rows(events)
    summary = queue_summary(rows)
    document = {
        "event_count": sum(1 for ev in events if ev.get("event") == "QUEUE_DEPTH"),
        "token_count": len(rows),
        "summary": summary,
        "rows": rows,
    }
    (out_dir / "queue.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    write_csv(out_dir / "queue.csv", rows)
    return document


def load_decode_events(trace_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not trace_dir.is_dir():
        return events
    for path in sorted(trace_dir.glob("*.jsonl")):
        for ev in load_jsonl(path):
            if ev.get("phase") == "decode":
                events.append(ev)
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract decode queue depth CSV")
    parser.add_argument("trace_dir", type=Path, help="Directory containing decode *.jsonl")
    parser.add_argument("-o", "--output", type=Path, help="Output directory")
    args = parser.parse_args()
    events = load_decode_events(args.trace_dir)
    out = args.output or args.trace_dir
    doc = merge_queue(events, out)
    print(json.dumps({"tokens": doc["token_count"], "summary": doc["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
