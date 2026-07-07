#!/usr/bin/env python3
"""Merge perf trace JSONL files into analysis artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.is_file():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def load_trace_dir(trace_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not trace_dir.is_dir():
        return events
    for path in sorted(trace_dir.glob("*.jsonl")):
        events.extend(load_jsonl(path))
    return events


def _ms(us: int | float | None) -> float | None:
    if us is None:
        return None
    return round(float(us) / 1000.0, 3)


def aggregate_bottleneck(events: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, float] = defaultdict(float)
    for ev in events:
        if ev.get("kind") != "span":
            continue
        if ev.get("phase") not in (None, "", "decode"):
            continue
        cat = str(ev.get("category", "UNKNOWN"))
        dur = ev.get("dur_us")
        if isinstance(dur, (int, float)) and dur > 0:
            totals[cat] += float(dur)
    total = sum(totals.values()) or 1.0
    pct = {k: round(100.0 * v / total, 2) for k, v in sorted(totals.items(), key=lambda x: -x[1])}
    unknown = pct.get("UNKNOWN", 0.0)
    return {
        "category_us": {k: int(v) for k, v in totals.items()},
        "category_pct": pct,
        "unknown_pct": unknown,
        "explained_pct": round(100.0 - unknown, 2),
    }


def token_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_token: dict[int, dict[str, Any]] = defaultdict(dict)
    for ev in events:
        if ev.get("phase") != "decode":
            continue
        tok = int(ev.get("token_idx", -1))
        if tok < 0:
            continue
        row = by_token[tok]
        row["token"] = tok
        row.setdefault("trace_id", ev.get("trace_id", ""))
        stage = str(ev.get("stage", ""))
        cat = str(ev.get("category", ""))
        event = str(ev.get("event", ""))

        if event == "QUEUE_DEPTH":
            attrs = ev.get("attrs") or {}
            if isinstance(attrs, dict):
                row[f"{stage}_queue_depth"] = attrs.get("depth")
            continue

        dur_ms = _ms(ev.get("dur_us"))
        if dur_ms is None:
            continue
        if cat == "COMPUTE" and stage:
            key = f"{stage}_compute_ms"
            row[key] = row.get(key, 0.0) + dur_ms
        elif cat == "NETWORK" and event == "HIDDEN_TRANSFER":
            row["network_ms"] = row.get("network_ms", 0.0) + dur_ms
            attrs = ev.get("attrs") or {}
            if isinstance(attrs, dict):
                row["payload_bytes"] = attrs.get("payload_bytes", row.get("payload_bytes"))
                ser_us = attrs.get("serialize_us")
                if isinstance(ser_us, (int, float)):
                    row["serialize_ms"] = row.get("serialize_ms", 0.0) + float(ser_us) / 1000.0
        elif cat == "SERIALIZATION":
            row["serialize_ms"] = row.get("serialize_ms", 0.0) + dur_ms
        elif cat == "SAMPLING":
            row["sampling_ms"] = row.get("sampling_ms", 0.0) + dur_ms
        elif cat in ("WAIT", "IDLE"):
            row[f"{stage}_wait_ms"] = row.get(f"{stage}_wait_ms", 0.0) + dur_ms
    rows = []
    for tok in sorted(by_token):
        row = by_token[tok]
        parts = [
            row.get("entry_compute_ms", 0.0),
            row.get("middle_compute_ms", 0.0),
            row.get("final_compute_ms", 0.0),
            row.get("network_ms", 0.0),
            row.get("entry_wait_ms", 0.0),
            row.get("middle_wait_ms", 0.0),
            row.get("final_wait_ms", 0.0),
            row.get("serialize_ms", 0.0),
            row.get("sampling_ms", 0.0),
        ]
        row["total_ms"] = round(sum(float(p or 0.0) for p in parts), 3)
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def merge_trace_dir(trace_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    from perf_trace.queue import merge_queue, queue_summary  # noqa: WPS433

    out = out_dir or trace_dir
    out.mkdir(parents=True, exist_ok=True)
    events = load_trace_dir(trace_dir)
    rows = token_rows(events)
    bottleneck = aggregate_bottleneck(events)
    queue_doc = merge_queue(events, out)
    document = {
        "trace_dir": str(trace_dir),
        "event_count": len(events),
        "token_count": len(rows),
        "bottleneck": bottleneck,
        "queue": queue_doc.get("summary", queue_summary(rows)),
        "events": events,
        "tokens": rows,
    }
    (out / "trace.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    write_csv(out / "tokens.csv", rows)
    (out / "bottleneck.json").write_text(json.dumps(bottleneck, indent=2), encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge Task 12 perf trace JSONL")
    parser.add_argument("trace_dir", type=Path, help="Directory containing *.jsonl traces")
    parser.add_argument("-o", "--output", type=Path, help="Output directory")
    args = parser.parse_args()
    doc = merge_trace_dir(args.trace_dir, args.output)
    print(json.dumps({
        "events": doc["event_count"],
        "tokens": doc["token_count"],
        "bottleneck": doc["bottleneck"].get("category_pct", {}),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
