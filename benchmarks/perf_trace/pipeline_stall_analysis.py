#!/usr/bin/env python3
"""Task 12.1 — pipeline stall / bubble analysis from existing perf traces (read-only)."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

NODES = {"entry": "node-a", "middle": "node-b", "final": "node-c"}


def load_deduped(trace_dir: Path, trace_id: str, node_id: str) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    events: list[dict[str, Any]] = []
    for path in sorted(trace_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("trace_id") != trace_id or ev.get("phase") != "decode":
                continue
            if ev.get("node_id") != node_id:
                continue
            key = (ev.get("event"), ev.get("WaveID", ev.get("token_idx")), ev.get("ts_us"), ev.get("kind"))
            if key in seen:
                continue
            seen.add(key)
            events.append(ev)
    return sorted(events, key=lambda e: e["ts_us"])


def find_fast_session(entry_receives: list[dict[str, Any]], *, min_tokens: int = 6) -> list[dict[str, Any]]:
    clusters: list[list[dict[str, Any]]] = []
    cur = [entry_receives[0]] if entry_receives else []
    for ev in entry_receives[1:]:
        if (ev["ts_us"] - cur[-1]["ts_us"]) / 1000.0 > 2000:
            clusters.append(cur)
            cur = [ev]
        else:
            cur.append(ev)
    if cur:
        clusters.append(cur)
    cands = [c for c in clusters if len(c) >= min_tokens]
    if not cands:
        return []
    return min(cands, key=lambda c: c[-1]["ts_us"] - c[0]["ts_us"])


def analyze_trace(trace_dir: Path, trace_id: str) -> dict[str, Any] | None:
    streams = {stage: load_deduped(trace_dir, trace_id, node) for stage, node in NODES.items()}
    entry_recv = [e for e in streams["entry"] if e.get("event") == "ENTRY_RECEIVE"]
    session = find_fast_session(entry_recv)
    if not session:
        return None

    w0 = session[0]["ts_us"] - 100_000
    w1 = session[-1]["ts_us"] + 5_000_000
    win = {stage: [e for e in evs if w0 <= e["ts_us"] <= w1] for stage, evs in streams.items()}
    n = len(session)

    ordinals: dict[str, list[dict[str, Any]]] = {}
    for stage in NODES:
        receives: list[dict[str, Any]] = []
        seen_ts: set[int] = set()
        for ev in win[stage]:
            if "RECEIVE" not in str(ev.get("event", "")):
                continue
            if ev["ts_us"] in seen_ts:
                continue
            seen_ts.add(ev["ts_us"])
            receives.append(ev)
        receives = receives[:n]
        compute = {
            (ev.get("WaveID") if isinstance(ev.get("WaveID"), int) and ev.get("WaveID") >= 0 else ev.get("token_idx")): ev
            for ev in win[stage]
            if str(ev.get("event", "")).endswith("COMPUTE_END")
        }
        rows: list[dict[str, Any]] = []
        for recv in receives:
            tok = recv.get("WaveID") if isinstance(recv.get("WaveID"), int) and recv.get("WaveID") >= 0 else recv.get("token_idx")
            comp = compute.get(tok)
            dur_us = int(comp.get("dur_us", 0)) if comp else 0
            send_ts = None
            for ev in win[stage]:
                ev_key = ev.get("WaveID") if isinstance(ev.get("WaveID"), int) and ev.get("WaveID") >= 0 else ev.get("token_idx")
                if ev_key != tok:
                    continue
                if ev.get("event") not in ("ENTRY_SEND_END", "HIDDEN_TRANSFER", "MIDDLE_SEND_END"):
                    continue
                if send_ts is None or ev["ts_us"] > send_ts:
                    send_ts = ev["ts_us"]
            rows.append({
                "WaveID": recv.get("WaveID"),
                "token_idx": tok,
                "recv_us": recv["ts_us"],
                "compute_ms": dur_us / 1000.0,
                "compute_end_us": recv["ts_us"] + dur_us,
                "send_us": send_ts,
            })
        ordinals[stage] = rows

    periods = [
        (ordinals["entry"][i]["recv_us"] - ordinals["entry"][i - 1]["recv_us"]) / 1000.0
        for i in range(1, n)
    ]
    critical = [
        (ordinals["final"][i]["compute_end_us"] - ordinals["entry"][i]["recv_us"]) / 1000.0
        for i in range(n)
    ]
    bubbles = [periods[i - 1] - critical[i] for i in range(1, n)]

    hops_em: list[float] = []
    hops_mf: list[float] = []
    for i in range(n):
        e = ordinals["entry"][i]
        m = ordinals["middle"][i]
        f = ordinals["final"][i]
        if e.get("send_us") and m.get("recv_us"):
            hops_em.append((m["recv_us"] - e["send_us"]) / 1000.0)
        if m.get("send_us") and f.get("recv_us"):
            hops_mf.append((f["recv_us"] - m["send_us"]) / 1000.0)

    base = ordinals["entry"][0]["recv_us"]
    timeline: list[dict[str, Any]] = []
    for i in range(n):
        row: dict[str, Any] = {
            "ordinal": i,
            "WaveID": ordinals["entry"][i].get("WaveID"),
            "token_idx": ordinals["entry"][i]["token_idx"],
        }
        for stage in NODES:
            o = ordinals[stage][i]
            row[f"{stage}_recv_ms"] = round((o["recv_us"] - base) / 1000.0, 2)
            row[f"{stage}_compute_ms"] = round(o["compute_ms"], 2)
            row[f"{stage}_end_ms"] = round((o["compute_end_us"] - base) / 1000.0, 2)
        if i > 0:
            row["entry_period_ms"] = round(periods[i - 1], 2)
            row["critical_path_ms"] = round(critical[i - 1], 2)
            row["bubble_ms"] = round(bubbles[i - 1], 2)
        timeline.append(row)

    return {
        "trace_id": trace_id,
        "token_count": n,
        "session_wall_ms": round((session[-1]["ts_us"] - session[0]["ts_us"]) / 1000.0, 2),
        "avg_entry_period_ms": round(statistics.mean(periods), 2) if periods else 0,
        "avg_critical_path_ms": round(statistics.mean(critical[1:]), 2) if len(critical) > 1 else 0,
        "avg_bubble_ms": round(statistics.mean(bubbles), 2) if bubbles else 0,
        "avg_compute_ms": {
            stage: round(statistics.mean([o["compute_ms"] for o in ordinals[stage]]), 2)
            for stage in NODES
        },
        "avg_hop_entry_middle_ms": round(statistics.mean(hops_em), 3) if hops_em else 0,
        "avg_hop_middle_final_ms": round(statistics.mean(hops_mf), 3) if hops_mf else 0,
        "timeline": timeline,
    }


def ascii_gantt(row: dict[str, Any], width: int = 56) -> list[str]:
    period = row.get("entry_period_ms") or 55.0
    scale = width / max(period, 1.0)

    def bar(start: float, dur: float, ch: str) -> str:
        lo = max(0, int(start * scale))
        hi = min(width, int((start + dur) * scale))
        out = [" "] * width
        for i in range(lo, max(lo + 1, hi)):
            out[i] = ch
        return "".join(out)

    lines = []
    for stage, ch in (("entry", "E"), ("middle", "M"), ("final", "F")):
        start = row[f"{stage}_recv_ms"] - (row[f"{stage}_recv_ms"] if row["ordinal"] == 0 else 0)
        # relative within period window ending at next entry recv
        start = row[f"{stage}_recv_ms"] % period if row.get("entry_period_ms") else row[f"{stage}_recv_ms"]
        if row["ordinal"] > 0 and row.get("entry_period_ms"):
            start = row[f"{stage}_recv_ms"] - (row[f"{stage}_recv_ms"] - row.get("entry_period_ms", period))
        recv = row[f"{stage}_recv_ms"]
        if row["ordinal"] == 0:
            base = 0.0
        else:
            base = row[f"{stage}_recv_ms"] - row.get("entry_period_ms", period)
        rel_start = recv - base if row["ordinal"] else recv
        lines.append(f"{stage:6} |{bar(rel_start, row[f'{stage}_compute_ms'], ch)}| {row[f'{stage}_compute_ms']:.1f}ms")
    bubble = row.get("bubble_ms")
    if bubble is not None:
        bstart = row.get("critical_path_ms", 0)
        lines.append(f"bubble |{bar(bstart, bubble, '.')}| {bubble:.1f}ms idle")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline stall analysis")
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("--trace", default="trace-000002")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    doc = analyze_trace(args.raw_dir, args.trace)
    if not doc:
        print(json.dumps({"error": "no session found"}))
        return 1

    if args.output:
        args.output.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
