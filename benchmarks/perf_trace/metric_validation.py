#!/usr/bin/env python3
"""Task 13.1 — performance metric validation (instrumentation only, no runtime changes).

Every aggregated benchmark metric must declare its source events, formula, trace_id,
and validity status. Metrics with incomplete spans are UNKNOWN, not FAIL/PASS.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from perf_trace.merge import load_jsonl, wave_correlation_key

ValidationStatus = Literal["PASS", "FAIL", "UNKNOWN", "INVALID", "SKIP"]

STAGES = ("entry", "middle", "final")

# Minimal decode-phase event chain per stage (Acceptance Criteria §1).
STAGE_DECODE_CHAIN: dict[str, tuple[str, ...]] = {
    "entry": (
        "ENTRY_RECEIVE",
        "ENTRY_COMPUTE_BEGIN",
        "ENTRY_COMPUTE_END",
        "ENTRY_SEND_END",
    ),
    "middle": (
        "MIDDLE_RECEIVE",
        "MIDDLE_COMPUTE_BEGIN",
        "MIDDLE_COMPUTE_END",
        "MIDDLE_SEND_END",
    ),
    "final": (
        "FINAL_RECEIVE",
        "FINAL_COMPUTE_BEGIN",
        "FINAL_COMPUTE_END",
        "SAMPLER_END",
    ),
}

CLIENT_EVENTS = ("CLIENT_RESPONSE", "GENERATE_END", "CLIENT_TTFT")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_raw_events(raw_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not raw_dir.is_dir():
        return events
    for path in sorted(raw_dir.glob("*.jsonl")):
        events.extend(load_jsonl(path))
    return events


def filter_trace(events: list[dict[str, Any]], trace_id: str) -> list[dict[str, Any]]:
    return [e for e in events if str(e.get("trace_id", "")) == trace_id]


def stage_span_coverage(
        events: list[dict[str, Any]],
        stage: str,
        *,
        required_phase: str = "decode",
) -> dict[str, Any]:
    """Check whether a stage emits the full decode chain in the required phase."""
    chain = STAGE_DECODE_CHAIN[stage]
    decode_hits = {ev: 0 for ev in chain}
    mislabeled: dict[str, int] = defaultdict(int)
    for ev in events:
        if str(ev.get("stage", "")) != stage:
            continue
        name = str(ev.get("event", ""))
        if name not in chain:
            continue
        phase = str(ev.get("phase", ""))
        if phase == required_phase:
            decode_hits[name] += 1
        else:
            mislabeled[name] += 1

    missing = [ev for ev, n in decode_hits.items() if n < 1]
    status: ValidationStatus = "PASS"
    reason: str | None = None
    if missing:
        if mislabeled and not any(decode_hits.values()):
            status = "FAIL"
            reason = (
                f"missing decode spans; found mislabeled in other phases: "
                f"{dict(mislabeled)}"
            )
        else:
            status = "FAIL"
            reason = f"missing decode spans: {missing}"
    return {
        "stage": stage,
        "status": status,
        "required_phase": required_phase,
        "events_found": decode_hits,
        "mislabeled_phases": mislabeled,
        "missing_events": missing,
        "reason": reason,
    }


def pick_primary_trace_id(
        events: list[dict[str, Any]],
        *,
        prefer: str | None = None,
) -> str | None:
    if prefer and any(e.get("trace_id") == prefer for e in events):
        return prefer
    counts: dict[str, int] = {}
    for ev in events:
        if ev.get("phase") != "decode":
            continue
        tid = str(ev.get("trace_id", ""))
        if tid.startswith("trace-"):
            counts[tid] = counts.get(tid, 0) + 1
    if not counts:
        return prefer
    return max(counts, key=counts.get)


def _events_for_wave(
        events: list[dict[str, Any]],
        wave_id: int,
        stage: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        if str(ev.get("stage", "")) != stage:
            continue
        key = wave_correlation_key(ev)
        if key != wave_id:
            continue
        out.append(ev)
    return sorted(out, key=lambda e: int(e.get("ts_us") or 0))


def build_token_chain(
        events: list[dict[str, Any]],
        wave_id: int,
        *,
        phase: str = "decode",
) -> dict[str, Any]:
    """Build the per-token pipeline chain (AC §1)."""
    chain: dict[str, Any] = {"WaveID": wave_id, "phase_filter": phase, "stages": {}}
    for stage in STAGES:
        stage_events = [
            e for e in _events_for_wave(events, wave_id, stage)
            if str(e.get("phase", "")) == phase
        ]
        steps: list[dict[str, Any]] = []
        for name in STAGE_DECODE_CHAIN[stage]:
            matched = [e for e in stage_events if e.get("event") == name]
            if not matched:
                steps.append({"event": name, "present": False})
                continue
            ev = matched[0]
            steps.append({
                "event": name,
                "present": True,
                "ts_us": ev.get("ts_us"),
                "dur_us": ev.get("dur_us"),
                "dur_ms": round(float(ev.get("dur_us") or 0) / 1000.0, 3),
            })
        chain["stages"][stage] = steps
    client = [
        e for e in events
        if str(e.get("phase", "")) == phase and str(e.get("event", "")) in CLIENT_EVENTS
    ]
    chain["client"] = [
        {"event": e.get("event"), "ts_us": e.get("ts_us"), "component": e.get("component")}
        for e in client[:5]
    ]
    complete = all(
        step.get("present")
        for stage in STAGES
        for step in chain["stages"][stage]
    )
    chain["complete"] = complete
    return chain


def _span_dur_ms(events: list[dict[str, Any]], stage: str, end_event: str) -> float | None:
    for ev in events:
        if ev.get("event") != end_event or str(ev.get("stage", "")) != stage:
            continue
        dur = ev.get("dur_us")
        if isinstance(dur, (int, float)) and dur > 0:
            return float(dur) / 1000.0
    return None


def _instant_ts(events: list[dict[str, Any]], stage: str, instant_event: str) -> int | None:
    for ev in events:
        if ev.get("event") != instant_event or str(ev.get("stage", "")) != stage:
            continue
        ts = ev.get("ts_us")
        if isinstance(ts, int):
            return ts
    return None


def compute_critical_path_tokens(
        events: list[dict[str, Any]],
        *,
        phase: str = "decode",
) -> dict[str, Any]:
    """Critical path per wave: entry_recv → final_compute_end (RFC §19)."""
    waves: set[int] = set()
    for ev in events:
        if str(ev.get("phase", "")) != phase:
            continue
        w = wave_correlation_key(ev)
        if w is not None:
            waves.add(w)

    rows: list[dict[str, Any]] = []
    phase_events = [e for e in events if str(e.get("phase", "")) == phase]
    for wave in sorted(waves):
        entry_recv = _instant_ts(_events_for_wave(phase_events, wave, "entry"), "entry", "ENTRY_RECEIVE")
        entry_comp = _span_dur_ms(_events_for_wave(phase_events, wave, "entry"), "entry", "ENTRY_COMPUTE_END")
        middle_comp = _span_dur_ms(_events_for_wave(phase_events, wave, "middle"), "middle", "MIDDLE_COMPUTE_END")
        final_comp = _span_dur_ms(_events_for_wave(phase_events, wave, "final"), "final", "FINAL_COMPUTE_END")

        final_end_ts = None
        final_recv = _instant_ts(_events_for_wave(phase_events, wave, "final"), "final", "FINAL_RECEIVE")
        if final_recv is not None and final_comp is not None:
            final_end_ts = final_recv + int(final_comp * 1000)

        wall_critical = None
        if entry_recv is not None and final_end_ts is not None:
            wall_critical = (final_end_ts - entry_recv) / 1000.0

        sum_compute = None
        parts = [entry_comp, middle_comp, final_comp]
        if all(p is not None for p in parts):
            sum_compute = sum(parts)

        rows.append({
            "WaveID": wave,
            "entry_compute_ms": entry_comp,
            "middle_compute_ms": middle_comp,
            "final_compute_ms": final_comp,
            "sum_compute_ms": sum_compute,
            "wall_critical_path_ms": wall_critical,
        })

    valid = [r for r in rows if r.get("wall_critical_path_ms") is not None]
    sum_valid = [r for r in rows if r.get("sum_compute_ms") is not None]

    return {
        "phase": phase,
        "token_rows": rows,
        "avg_wall_critical_path_ms": round(statistics.mean(
            [r["wall_critical_path_ms"] for r in valid]
        ), 3) if valid else None,
        "avg_sum_compute_ms": round(statistics.mean(
            [r["sum_compute_ms"] for r in sum_valid]
        ), 3) if sum_valid else None,
        "complete_count": len(valid),
        "wave_count": len(rows),
    }


def compute_bubble_from_entry_periods(
        events: list[dict[str, Any]],
        critical_rows: list[dict[str, Any]],
        *,
        phase: str = "decode",
) -> dict[str, Any]:
    """Bubble = entry_period - critical_path (RFC §19). Requires aligned waves."""
    recv_events = []
    for ev in events:
        if str(ev.get("phase", "")) != phase:
            continue
        if ev.get("event") != "ENTRY_RECEIVE" or str(ev.get("stage", "")) != "entry":
            continue
        w = wave_correlation_key(ev)
        ts = ev.get("ts_us")
        if w is None or not isinstance(ts, int):
            continue
        recv_events.append((w, ts))
    recv_events.sort(key=lambda x: x[1])

    crit_by_wave = {
        r["WaveID"]: r.get("wall_critical_path_ms")
        for r in critical_rows
        if r.get("wall_critical_path_ms") is not None
    }

    periods: list[float] = []
    bubbles: list[float] = []
    for i in range(1, len(recv_events)):
        w_prev, ts_prev = recv_events[i - 1]
        w_cur, ts_cur = recv_events[i]
        period_ms = (ts_cur - ts_prev) / 1000.0
        periods.append(period_ms)
        crit = crit_by_wave.get(w_cur)
        if crit is not None:
            bubbles.append(period_ms - crit)

    if not bubbles or not periods:
        return {
            "status": "UNKNOWN",
            "reason": "missing aligned entry recv / critical path spans",
            "avg_period_ms": round(statistics.mean(periods), 3) if periods else None,
            "avg_bubble_ms": None,
            "bubble_pct": None,
        }

    avg_period = statistics.mean(periods)
    avg_bubble = statistics.mean(bubbles)
    bubble_pct = 100.0 * avg_bubble / avg_period if avg_period > 0 else None
    return {
        "status": "PASS" if bubble_pct is not None else "UNKNOWN",
        "reason": None,
        "avg_period_ms": round(avg_period, 3),
        "avg_bubble_ms": round(avg_bubble, 3),
        "bubble_pct": round(bubble_pct, 2) if bubble_pct is not None else None,
        "formula": "bubble_ms = entry_period_ms - wall_critical_path_ms; "
                   "bubble_pct = bubble_ms / entry_period_ms × 100",
        "sample_count": len(bubbles),
    }


def compute_tps_from_timing(timing: dict[str, Any]) -> dict[str, Any]:
    """Single source of truth: orchestrator-reported decode timing (AC §4)."""
    decode_ms = timing.get("decode_ms")
    tokens = timing.get("generated_tokens") or timing.get("token_count")
    if not isinstance(decode_ms, (int, float)) or not isinstance(tokens, (int, float)):
        return {
            "status": "UNKNOWN",
            "reason": "missing decode_ms or generated_tokens in orchestrator timing",
            "value": None,
            "source": "orchestrator_api",
            "formula": "TPS = generated_tokens / decode_ms × 1000",
        }
    if decode_ms <= 0 or tokens <= 0:
        return {
            "status": "INVALID",
            "reason": "decode_ms or token count non-positive",
            "value": None,
            "source": "orchestrator_api",
            "formula": "TPS = generated_tokens / decode_ms × 1000",
        }
    tps = float(tokens) / float(decode_ms) * 1000.0
    return {
        "status": "PASS",
        "reason": None,
        "value": round(tps, 3),
        "decode_ms": float(decode_ms),
        "generated_tokens": int(tokens),
        "source": "orchestrator_api",
        "formula": "TPS = generated_tokens / decode_ms × 1000",
        "trace_id": timing.get("trace_id"),
    }


def compute_ceiling_tps(critical: dict[str, Any]) -> dict[str, Any]:
    """Ceiling = 1000 / critical_path_ms (AC §6). Uses wall critical path when complete."""
    avg = critical.get("avg_wall_critical_path_ms")
    if avg is None or avg <= 0:
        alt = critical.get("avg_sum_compute_ms")
        if alt is None or alt <= 0:
            return {
                "status": "UNKNOWN",
                "reason": "critical path not computable from decode spans",
                "value": None,
                "formula": "ceiling_tps = 1000 / avg_wall_critical_path_ms",
            }
        avg = alt
        formula = "ceiling_tps = 1000 / avg(entry+middle+final compute_ms) [fallback]"
    else:
        formula = "ceiling_tps = 1000 / avg_wall_critical_path_ms"

    return {
        "status": "PASS",
        "reason": None,
        "value": round(1000.0 / avg, 3),
        "critical_path_ms": round(avg, 3),
        "formula": formula,
    }


def cross_check_tps_vs_ceiling(tps_doc: dict[str, Any], ceiling_doc: dict[str, Any]) -> dict[str, Any]:
    """If measured TPS > ceiling, mark METRIC INVALID (AC §8)."""
    tps = tps_doc.get("value")
    ceiling = ceiling_doc.get("value")
    if tps is None or ceiling is None:
        return {
            "status": "SKIP",
            "reason": "TPS or ceiling UNKNOWN — cross-check skipped",
        }
    if tps > ceiling * 1.05:  # 5% tolerance for rounding
        return {
            "status": "INVALID",
            "reason": f"measured TPS ({tps}) > theoretical ceiling ({ceiling}) — inconsistent metrics",
            "measured_tps": tps,
            "ceiling_tps": ceiling,
        }
    return {
        "status": "PASS",
        "reason": None,
        "measured_tps": tps,
        "ceiling_tps": ceiling,
        "ratio": round(tps / ceiling, 3) if ceiling else None,
    }


def extract_generate_timing(results_path: Path | None) -> dict[str, Any] | None:
    if not results_path or not results_path.is_file():
        return None
    doc = _read_json(results_path)
    for sc in doc.get("scenarios", []):
        for st in sc.get("stages", []):
            if st.get("name") != "generate":
                continue
            metrics = st.get("metrics") or {}
            timing = metrics.get("timing") or {}
            if timing:
                timing = dict(timing)
                timing.setdefault("generated_tokens", metrics.get("token_count"))
                timing.setdefault("trace_id", metrics.get("trace_id") or timing.get("trace_id"))
                return timing
    return None


def run_metric_validation(
        raw_dir: Path,
        analysis_dir: Path,
        *,
        trace_id: str | None = None,
        generate_timing: dict[str, Any] | None = None,
        results_path: Path | None = None,
        observability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_dir = Path(raw_dir)
    analysis_dir = Path(analysis_dir)
    events = load_raw_events(raw_dir)

    if generate_timing is None:
        generate_timing = extract_generate_timing(results_path)

    tid = pick_primary_trace_id(events, prefer=trace_id or (
        str(generate_timing.get("trace_id")) if generate_timing else None
    ))
    trace_events = filter_trace(events, tid) if tid else []

    stage_checks = [
        stage_span_coverage(trace_events, stage)
        for stage in STAGES
    ]
    all_stages_pass = all(c["status"] == "PASS" for c in stage_checks)

    critical = compute_critical_path_tokens(trace_events, phase="decode")
    bubble = compute_bubble_from_entry_periods(
        trace_events,
        critical.get("token_rows") or [],
        phase="decode",
    )
    if not all_stages_pass:
        bubble = {
            **bubble,
            "status": "UNKNOWN",
            "reason": "missing decode spans on one or more stages",
        }

    tps_doc = compute_tps_from_timing(generate_timing or {})
    ceiling_doc = compute_ceiling_tps(critical)
    if not all_stages_pass:
        ceiling_doc = {
            **ceiling_doc,
            "status": "UNKNOWN",
            "reason": "missing decode spans — ceiling not certified",
            "value": None,
        }

    tps_cross = cross_check_tps_vs_ceiling(tps_doc, ceiling_doc)

    # Sample token chain: wave 17 if present, else last complete wave
    sample_wave = 17 if any(wave_correlation_key(e) == 17 for e in trace_events) else None
    if sample_wave is None and critical.get("token_rows"):
        sample_wave = critical["token_rows"][-1].get("WaveID")
    token_chain = (
        build_token_chain(trace_events, int(sample_wave))
        if sample_wave is not None else None
    )

    util_doc = (observability or {}).get("utilization") or {}
    sched_doc = (observability or {}).get("scheduler") or {}
    net_doc = (observability or {}).get("network") or {}
    ser_doc = (observability or {}).get("serialization") or {}

    metric_status = {
        "tps": tps_doc,
        "critical_path": {
            "status": "PASS" if critical.get("avg_wall_critical_path_ms") else "UNKNOWN",
            "reason": None if critical.get("avg_wall_critical_path_ms") else "missing decode spans",
            **critical,
        },
        "ceiling_tps": ceiling_doc,
        "bubble": bubble,
        "tps_vs_ceiling": tps_cross,
        "utilization": util_doc if util_doc else {
            "status": "UNKNOWN" if not all_stages_pass else "SKIP",
            "reason": "utilization requires full-stage decode spans",
        },
        "scheduler": sched_doc if sched_doc else {
            "status": "UNKNOWN" if not all_stages_pass else "SKIP",
            "reason": "scheduler breakdown requires full-stage decode spans",
        },
        "serialization": ser_doc if ser_doc else {
            "status": "UNKNOWN" if not all_stages_pass else "SKIP",
            "reason": "serialization spans require full decode trace",
        },
        "network": net_doc if net_doc else {
            "status": "UNKNOWN" if not all_stages_pass else "SKIP",
            "reason": "network hops require full decode trace",
        },
    }

    checks: list[dict[str, Any]] = []
    for sc in stage_checks:
        checks.append({
            "name": f"decode_trace_{sc['stage']}",
            "status": sc["status"],
            "reason": sc.get("reason"),
            "details": sc,
        })

    checks.append({
        "name": "critical_path_present",
        "status": "PASS" if critical.get("avg_wall_critical_path_ms") else "UNKNOWN",
        "reason": None if critical.get("avg_wall_critical_path_ms") else "missing decode spans",
    })
    checks.append({
        "name": "bubble_measurable",
        "status": bubble.get("status", "UNKNOWN"),
        "reason": bubble.get("reason"),
    })
    checks.append({
        "name": "utilization_measurable",
        "status": util_doc.get("status", "UNKNOWN" if not all_stages_pass else "SKIP"),
        "reason": util_doc.get("reason"),
    })
    checks.append({
        "name": "scheduler_measurable",
        "status": sched_doc.get("status", "UNKNOWN" if not all_stages_pass else "SKIP"),
        "reason": sched_doc.get("reason"),
    })
    checks.append({
        "name": "tps_vs_ceiling",
        "status": tps_cross.get("status", "SKIP"),
        "reason": tps_cross.get("reason"),
    })

    # Overall: FAIL if any stage trace missing; INVALID if cross-check fails
    overall: ValidationStatus = "PASS"
    if any(c["status"] == "FAIL" for c in checks if c["name"].startswith("decode_trace_")):
        overall = "FAIL"
    if tps_cross.get("status") == "INVALID":
        overall = "INVALID"
    if not all_stages_pass and overall == "PASS":
        overall = "FAIL"

    document = {
        "task": "14",
        "trace_id": tid,
        "overall": overall,
        "checks": checks,
        "metrics": metric_status,
        "token_chain_sample": token_chain,
        "spec": "docs/PERFORMANCE_METRICS_SPEC.md",
    }
    return document


def build_validation_md(doc: dict[str, Any]) -> str:
    lines = [
        "# Task 14 Metric Validation",
        "",
        f"**Trace ID:** `{doc.get('trace_id', '—')}`",
        f"**Overall:** **{doc.get('overall', 'UNKNOWN')}**",
        "",
        "## Stage Decode Trace",
        "",
        "| Check | Status | Reason |",
        "|-------|--------|--------|",
    ]
    for row in doc.get("checks", []):
        reason = row.get("reason") or "—"
        lines.append(f"| {row.get('name', '')} | **{row.get('status', '')}** | {reason} |")

    lines.extend(["", "## Metrics", ""])
    metrics = doc.get("metrics") or {}
    for key in ("tps", "critical_path", "ceiling_tps", "bubble", "tps_vs_ceiling"):
        m = metrics.get(key) or {}
        val = m.get("value") if "value" in m else m.get("bubble_pct")
        if val is None and key == "critical_path":
            val = m.get("avg_wall_critical_path_ms")
        status = m.get("status", "—")
        formula = m.get("formula", m.get("reason", ""))
        lines.append(f"### {key}")
        lines.append(f"- Status: **{status}**")
        if val is not None:
            lines.append(f"- Value: **{val}**")
        if formula:
            lines.append(f"- Note: {formula}")
        lines.append("")

    sample = doc.get("token_chain_sample")
    if sample:
        lines.extend(["## Sample Token Chain", "", f"WaveID **{sample.get('WaveID')}** complete={sample.get('complete')}", ""])
        for stage, steps in (sample.get("stages") or {}).items():
            present = sum(1 for s in steps if s.get("present"))
            lines.append(f"- {stage}: {present}/{len(steps)} events")
    lines.append("")
    return "\n".join(lines)


def write_validation(
        raw_dir: Path,
        analysis_dir: Path,
        *,
        trace_id: str | None = None,
        generate_timing: dict[str, Any] | None = None,
        results_path: Path | None = None,
        observability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis_dir = Path(analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    doc = run_metric_validation(
        raw_dir,
        analysis_dir,
        trace_id=trace_id,
        generate_timing=generate_timing,
        results_path=results_path,
        observability=observability,
    )
    (analysis_dir / "validation.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    (analysis_dir / "validation.md").write_text(build_validation_md(doc), encoding="utf-8")
    return doc


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Task 13.1 performance metric validation")
    parser.add_argument("--raw", type=Path, required=True, help="Raw perf trace JSONL directory")
    parser.add_argument("--analysis", type=Path, required=True, help="Analysis output directory")
    parser.add_argument("--results", type=Path, help="benchmark results.json")
    parser.add_argument("--trace-id", default="", help="Primary trace id")
    args = parser.parse_args()

    doc = write_validation(
        args.raw,
        args.analysis,
        trace_id=args.trace_id or None,
        results_path=args.results,
    )
    print(json.dumps({
        "overall": doc.get("overall"),
        "trace_id": doc.get("trace_id"),
        "checks": [
            {k: row[k] for k in ("name", "status", "reason")}
            for row in doc.get("checks", [])
        ],
    }, indent=2))
    return 0 if doc.get("overall") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
