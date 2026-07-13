#!/usr/bin/env python3
"""Task 17.1A — client decode-loop breakdown (orchestrator bubble attribution).

Consumes CLIENT_* spans emitted by node_agent's pipeline decode loop plus the
RUNTIME_FLAGS instant, and attributes the inter-token period observed on the
client node (single clock, no cross-node skew) to named waits.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from perf_trace.metric_validation import filter_trace, load_raw_events, pick_primary_trace_id

CLIENT_SPAN_EVENTS = {
    "token_wait": "CLIENT_TOKEN_WAIT_END",
    "complete_wait": "CLIENT_COMPLETE_WAIT_END",
    "ack_wait": "CLIENT_ACK_WAIT_END",
    "send": "CLIENT_SEND_END",
    "embed": "CLIENT_EMBED_END",
    "blocking_rt": "CLIENT_BLOCKING_RT_END",
}

FLAGS_EVENT = "RUNTIME_FLAGS"

# Waves at the start of decode are excluded from steady-state stats
# (pipeline fill / prefill boundary effects).
STEADY_MIN_WAVE = 2


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # perf_trace containers share one Docker volume for /data/models/perf_trace;
    # collecting raw/*.jsonl from every container copies identical files N
    # times. Same dedup key as pipeline_stall_analysis.load_deduped().
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for ev in events:
        key = (
            ev.get("event"),
            ev.get("node_id"),
            ev.get("WaveID", ev.get("token_idx")),
            ev.get("ts_us"),
            ev.get("kind"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def _ms(dur_us: Any) -> float | None:
    if not isinstance(dur_us, (int, float)):
        return None
    return float(dur_us) / 1000.0


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[idx]


def _stage_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0, "avg_ms": None, "min_ms": None, "max_ms": None,
            "p95_ms": None, "total_ms": None,
        }
    return {
        "count": len(values),
        "avg_ms": round(statistics.mean(values), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
        "p95_ms": round(_p95(values), 3),
        "total_ms": round(sum(values), 3),
    }


def _parse_attrs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _wave_key(ev: dict[str, Any]) -> int | None:
    # CLIENT_* spans are emitted from node_agent's own process-global trace
    # context; g_wave_id there is not synchronized to the client's own step
    # counter (it belongs to whichever worker-side decode context happened to
    # be active last), so WaveID is unreliable for these events. token_idx is
    # explicitly set per-span via set_token_idx() and is authoritative here.
    event = str(ev.get("event", ""))
    if event.startswith("CLIENT_"):
        t = ev.get("token_idx")
        return t if isinstance(t, int) and t >= 0 else None
    w = ev.get("WaveID")
    if isinstance(w, int) and w >= 0:
        return w
    t = ev.get("token_idx")
    if isinstance(t, int) and t >= 0:
        return t
    return None


def extract_runtime_flags(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for ev in events:
        if ev.get("event") == FLAGS_EVENT:
            attrs = _parse_attrs(ev.get("attrs"))
            if attrs:
                return attrs
    return None


def _split_generate_segments(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    # A benchmark scenario issues multiple /pipeline/generate calls under the
    # same trace_id (warmup, then measured) and each resets its own step
    # counter to 0 -- so token_idx alone collides across calls. RUNTIME_FLAGS
    # is emitted once per call (perf_trace_begin_generate) and is a reliable
    # call boundary; segment on it so per-wave buckets never mix two calls.
    flag_ts = sorted(
        ev["ts_us"] for ev in events
        if ev.get("event") == FLAGS_EVENT and isinstance(ev.get("ts_us"), int)
    )
    if len(flag_ts) <= 1:
        return [events]
    segments: list[list[dict[str, Any]]] = [[] for _ in flag_ts]
    for ev in events:
        ts = ev.get("ts_us")
        if not isinstance(ts, int):
            continue
        idx = 0
        for i, boundary in enumerate(flag_ts):
            if ts >= boundary:
                idx = i
        segments[idx].append(ev)
    return [seg for seg in segments if seg]


def build_client_loop_breakdown(
        events: list[dict[str, Any]],
        *,
        trace_id: str | None = None,
) -> dict[str, Any]:
    if trace_id:
        events = filter_trace(events, trace_id)
    events = _dedupe_events(events)

    flags = extract_runtime_flags(events)

    segments = _split_generate_segments(events)
    generate_call_count = len(segments)
    # Last segment = last /pipeline/generate call under this trace_id, i.e.
    # the measured run when warmup precedes it under the same trace_id.
    events = segments[-1]

    by_wave: dict[int, dict[str, float]] = {}
    wave_end_ts: dict[int, int] = {}
    for ev in events:
        if ev.get("phase") != "decode":
            continue
        name = ev.get("event")
        wave = _wave_key(ev)
        if wave is None:
            continue
        for stage, end_event in CLIENT_SPAN_EVENTS.items():
            if name != end_event:
                continue
            dur = _ms(ev.get("dur_us"))
            if dur is None:
                continue
            slot = by_wave.setdefault(wave, {})
            slot[stage] = slot.get(stage, 0.0) + dur
            if stage in ("token_wait", "blocking_rt"):
                ts = ev.get("ts_us")
                if isinstance(ts, int):
                    dur_us = ev.get("dur_us")
                    end = ts + int(dur_us) if isinstance(dur_us, (int, float)) else ts
                    wave_end_ts[wave] = end

    steady_waves = sorted(w for w in by_wave if w >= STEADY_MIN_WAVE)

    # Client-clock inter-token period: consecutive token-arrival timestamps.
    periods: dict[int, float] = {}
    ordered_ts = sorted((w, ts) for w, ts in wave_end_ts.items())
    for (w_prev, ts_prev), (w_cur, ts_cur) in zip(ordered_ts, ordered_ts[1:]):
        if w_cur == w_prev + 1 and w_cur >= STEADY_MIN_WAVE:
            periods[w_cur] = (ts_cur - ts_prev) / 1000.0

    stage_values: dict[str, list[float]] = {k: [] for k in CLIENT_SPAN_EVENTS}
    unattributed: list[float] = []
    attributed_rows: list[dict[str, Any]] = []
    for w in steady_waves:
        slot = by_wave[w]
        for stage in CLIENT_SPAN_EVENTS:
            if stage in slot:
                stage_values[stage].append(slot[stage])
        period = periods.get(w)
        if period is not None and period > 0:
            span_sum = sum(slot.values())
            gap = period - span_sum
            unattributed.append(gap)
            attributed_rows.append({
                "wave": w,
                "period_ms": round(period, 3),
                "span_sum_ms": round(span_sum, 3),
                "unattributed_ms": round(gap, 3),
                **{k: round(v, 3) for k, v in slot.items()},
            })

    period_values = [r["period_ms"] for r in attributed_rows]
    avg_period = statistics.mean(period_values) if period_values else None

    stages_out: dict[str, Any] = {}
    for stage, values in stage_values.items():
        st = _stage_stats(values)
        if avg_period and st["avg_ms"] is not None:
            st["pct_of_period"] = round(100.0 * st["avg_ms"] / avg_period, 2)
        stages_out[stage] = st
    gap_stats = _stage_stats(unattributed)
    if avg_period and gap_stats["avg_ms"] is not None:
        gap_stats["pct_of_period"] = round(100.0 * gap_stats["avg_ms"] / avg_period, 2)

    attribution_pct = None
    if avg_period and unattributed:
        attribution_pct = round(100.0 * (1.0 - statistics.mean(unattributed) / avg_period), 2)

    has_client_spans = any(v for v in stage_values.values())
    status = "PASS" if (has_client_spans and attributed_rows) else "UNKNOWN"

    return {
        "task": "17.1A",
        "trace_id": trace_id,
        "status": status,
        "runtime_flags": flags,
        "generate_call_count": generate_call_count,
        "steady_wave_count": len(steady_waves),
        "avg_period_ms": round(avg_period, 3) if avg_period else None,
        "stages": stages_out,
        "unattributed_gap": gap_stats,
        "attribution_pct_of_period": attribution_pct,
        "attribution_gate_90pct": (attribution_pct is not None and attribution_pct >= 90.0),
        "per_wave": attributed_rows,
    }


def build_client_loop_md(doc: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Task 17.1A — Client Decode Loop Breakdown")
    lines.append("")
    lines.append(f"**Trace ID:** `{doc.get('trace_id')}`")
    lines.append(f"**Status:** {doc.get('status')}")
    lines.append(f"**Steady waves:** {doc.get('steady_wave_count')}")
    lines.append(f"**Generate calls under this trace_id:** {doc.get('generate_call_count')} "
                 f"(analysis uses the last call, i.e. measured run when preceded by warmup)")
    lines.append("")
    flags = doc.get("runtime_flags")
    lines.append("## Runtime flags (recorded in trace)")
    lines.append("")
    if flags:
        for key, val in flags.items():
            lines.append(f"- **{key}**: `{val}`")
    else:
        lines.append("- MISSING — RUNTIME_FLAGS event not found (pre-17.1A binary?)")
    lines.append("")
    lines.append("## Period attribution (client clock, steady decode)")
    lines.append("")
    lines.append(f"**Avg inter-token period:** {doc.get('avg_period_ms')} ms")
    lines.append(f"**Attributed:** {doc.get('attribution_pct_of_period')}% "
                 f"(gate >= 90%: {'PASS' if doc.get('attribution_gate_90pct') else 'FAIL'})")
    lines.append("")
    lines.append("| Stage | avg (ms) | p95 | % of period | waves |")
    lines.append("|-------|---------:|----:|------------:|------:|")
    for stage, st in doc.get("stages", {}).items():
        if st.get("count", 0) == 0:
            continue
        lines.append(
            f"| {stage} | {st.get('avg_ms')} | {st.get('p95_ms')} | "
            f"{st.get('pct_of_period', '-')} | {st.get('count')} |")
    gap = doc.get("unattributed_gap", {})
    if gap.get("count", 0) > 0:
        lines.append(
            f"| *unattributed gap* | {gap.get('avg_ms')} | {gap.get('p95_ms')} | "
            f"{gap.get('pct_of_period', '-')} | {gap.get('count')} |")
    lines.append("")
    lines.append("Interpretation: `token_wait` is pipeline execution as seen by the client "
                 "(entry->final->token return); `complete_wait`, `ack_wait`, `send`, `embed` and the "
                 "unattributed gap are client/protocol overhead candidates for Task 17.1B.")
    lines.append("")
    return "\n".join(lines)


def _find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "docs").is_dir() and (candidate / "benchmarks").is_dir():
            return candidate
    return Path.cwd()


def write_client_loop_breakdown(
        raw_dir: Path,
        analysis_dir: Path,
        *,
        trace_id: str | None = None,
        docs_path: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = Path(analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    events = load_raw_events(Path(raw_dir))
    tid = trace_id or pick_primary_trace_id(events)
    doc = build_client_loop_breakdown(events, trace_id=tid)

    json_path = analysis_dir / "client_loop_breakdown.json"
    json_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    md_text = build_client_loop_md(doc)
    (analysis_dir / "client_loop_breakdown.md").write_text(md_text, encoding="utf-8")

    if docs_path is not None:
        docs_path.parent.mkdir(parents=True, exist_ok=True)
        docs_path.write_text(md_text, encoding="utf-8")
        doc["artifacts"] = {"json": str(json_path), "markdown": str(docs_path)}
    else:
        doc["artifacts"] = {"json": str(json_path)}
    return doc


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 17.1A client loop breakdown")
    parser.add_argument("raw_dir", type=Path, help="perf_trace raw event dir (*.jsonl)")
    parser.add_argument("--trace", dest="trace_id", default=None)
    parser.add_argument("--analysis-dir", type=Path, default=None)
    parser.add_argument("--docs", type=Path, default=None,
                        help="optional markdown report path (e.g. docs/TASK_17_1A_CLIENT_LOOP_BREAKDOWN.md)")
    args = parser.parse_args()

    analysis_dir = args.analysis_dir or (args.raw_dir.parent / "analysis")
    doc = write_client_loop_breakdown(
        args.raw_dir, analysis_dir, trace_id=args.trace_id, docs_path=args.docs)
    print(json.dumps({k: doc[k] for k in (
        "status", "trace_id", "runtime_flags", "avg_period_ms",
        "attribution_pct_of_period", "attribution_gate_90pct")}, indent=2))
    return 0 if doc["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
