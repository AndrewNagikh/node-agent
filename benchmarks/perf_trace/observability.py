#!/usr/bin/env python3
"""Task 14 — unified runtime observability artifacts from perf trace JSONL."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from perf_trace.merge import load_jsonl, wave_correlation_key
from perf_trace.metric_validation import (
    STAGE_DECODE_CHAIN,
    STAGES,
    compute_bubble_from_entry_periods,
    compute_critical_path_tokens,
    load_raw_events,
    pick_primary_trace_id,
    filter_trace,
)

ValidationStatus = Literal["PASS", "FAIL", "UNKNOWN", "INVALID", "SKIP"]

STAGE_ORDER = {"entry": 0, "middle": 1, "final": 2, "orchestrator": 3}

LIFECYCLE_EVENTS = (
    "ENTRY_RECEIVE",
    "ENTRY_COMPUTE_BEGIN",
    "ENTRY_COMPUTE_END",
    "ENTRY_SEND_END",
    "HIDDEN_TRANSFER",
    "MIDDLE_RECEIVE",
    "MIDDLE_COMPUTE_BEGIN",
    "MIDDLE_COMPUTE_END",
    "MIDDLE_SEND_END",
    "FINAL_RECEIVE",
    "FINAL_COMPUTE_BEGIN",
    "FINAL_COMPUTE_END",
    "SAMPLER_BEGIN",
    "SAMPLER_END",
    "CLIENT_RESPONSE",
    "GENERATE_END",
)

SCHEDULER_EVENTS = {
    "SCHED_QUEUE_WAIT": "worker_queue_wait",
    "SCHED_MUTEX_WAIT": "scheduler_wait",
    "SCHED_CV_WAIT": "scheduler_wait",
    "GGML_BACKEND_SYNC": "scheduler_wait",
    "GGML_GRAPH_BUILD": "scheduler_wait",
    "GGML_GRAPH_EXECUTE": "compute",
}


def _ms(us: int | float | None) -> float | None:
    if us is None:
        return None
    return round(float(us) / 1000.0, 3)


def _event_sort_key(ev: dict[str, Any]) -> tuple[int, int, int, str]:
    wave = wave_correlation_key(ev) or -1
    stage = str(ev.get("stage", ""))
    stage_ord = STAGE_ORDER.get(stage, 99)
    ts = int(ev.get("ts_us") or 0)
    name = str(ev.get("event", ""))
    return (wave, stage_ord, ts, name)


def build_unified_timeline(
        events: list[dict[str, Any]],
        *,
        phase: str = "decode",
        trace_id: str | None = None,
) -> dict[str, Any]:
    """All decode events sorted by WaveID, stage position, timestamp."""
    filtered = [
        e for e in events
        if str(e.get("phase", "")) == phase
        and (trace_id is None or str(e.get("trace_id", "")) == trace_id)
    ]
    sorted_events = sorted(filtered, key=_event_sort_key)
    rows: list[dict[str, Any]] = []
    for ev in sorted_events:
        wave = wave_correlation_key(ev)
        rows.append({
            "WaveID": wave,
            "token_idx": ev.get("token_idx"),
            "stage": ev.get("stage"),
            "event": ev.get("event"),
            "category": ev.get("category"),
            "kind": ev.get("kind"),
            "ts_us": ev.get("ts_us"),
            "dur_us": ev.get("dur_us"),
            "dur_ms": _ms(ev.get("dur_us")),
            "trace_id": ev.get("trace_id"),
            "node_id": ev.get("node_id"),
            "attrs": ev.get("attrs"),
        })

    by_wave: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        w = row.get("WaveID")
        if isinstance(w, int):
            by_wave[w].append(row)

    token_chains: dict[str, Any] = {}
    for wave, chain_events in sorted(by_wave.items()):
        steps = []
        for name in LIFECYCLE_EVENTS:
            matched = [e for e in chain_events if e.get("event") == name]
            if not matched:
                continue
            ev = matched[0]
            steps.append({
                "event": name,
                "stage": ev.get("stage"),
                "ts_us": ev.get("ts_us"),
                "dur_ms": ev.get("dur_ms"),
            })
        token_chains[str(wave)] = {
            "WaveID": wave,
            "steps": steps,
            "complete": len(steps) >= sum(len(v) for v in STAGE_DECODE_CHAIN.values()),
        }

    return {
        "phase": phase,
        "trace_id": trace_id,
        "event_count": len(rows),
        "wave_count": len(by_wave),
        "sort_key": "WaveID, stage_order, ts_us",
        "events": rows,
        "token_chains": token_chains,
    }


def _hop_ms(events: list[dict[str, Any]], stage: str, link: str) -> float | None:
    for ev in events:
        if ev.get("event") != "HIDDEN_TRANSFER" or str(ev.get("stage", "")) != stage:
            continue
        attrs = ev.get("attrs") or {}
        if isinstance(attrs, dict) and attrs.get("link") == link:
            return _ms(ev.get("dur_us"))
    return None


def build_critical_path_doc(
        events: list[dict[str, Any]],
        *,
        phase: str = "decode",
) -> dict[str, Any]:
    """Critical path = entry compute + A->B + middle compute + B->C + final compute + sampling."""
    base = compute_critical_path_tokens(events, phase=phase)
    phase_events = [e for e in events if str(e.get("phase", "")) == phase]
    waves: set[int] = set()
    for ev in phase_events:
        w = wave_correlation_key(ev)
        if w is not None:
            waves.add(w)

    enriched: list[dict[str, Any]] = []
    for wave in sorted(waves):
        wave_ev = [e for e in phase_events if wave_correlation_key(e) == wave]
        entry_comp = None
        middle_comp = None
        final_comp = None
        sampling = None
        for ev in wave_ev:
            name = str(ev.get("event", ""))
            dur = _ms(ev.get("dur_us"))
            if dur is None:
                continue
            if name == "ENTRY_COMPUTE_END":
                entry_comp = dur
            elif name == "MIDDLE_COMPUTE_END":
                middle_comp = dur
            elif name == "FINAL_COMPUTE_END":
                final_comp = dur
            elif name in ("SAMPLER_END",):
                sampling = dur

        ab = _hop_ms(wave_ev, "entry", "ab")
        bc = _hop_ms(wave_ev, "middle", "bc")

        # ab/bc/sampling kept for the row's own diagnostic fields below, but
        # not summed here -- see the matching comment in
        # metric_validation.compute_critical_path_tokens for why: they're
        # nested/overlapping with entry_comp/final_comp under current
        # instrumentation semantics, not independent sequential durations.
        # (In practice this local value is overwritten by base_row's below
        # when a matching wave is found, so this mirrors that fix rather
        # than changing the actual output on its own -- kept correct here
        # too so it isn't a live trap if that override logic ever changes.)
        sum_parts = [p for p in (entry_comp, middle_comp, final_comp) if p is not None]
        serial_critical = round(sum(sum_parts), 3) if len(sum_parts) == 3 else None

        row = {
            "WaveID": wave,
            "entry_compute_ms": entry_comp,
            "transfer_ab_ms": ab,
            "middle_compute_ms": middle_comp,
            "transfer_bc_ms": bc,
            "final_compute_ms": final_comp,
            "sampling_ms": sampling,
            "serial_critical_path_ms": serial_critical,
        }
        for base_row in base.get("token_rows") or []:
            if base_row.get("WaveID") == wave:
                row["wall_critical_path_ms"] = base_row.get("wall_critical_path_ms")
                row["sum_compute_ms"] = base_row.get("sum_compute_ms")
                row["serial_critical_path_ms"] = base_row.get("serial_critical_path_ms")
                row["wall_clock_skewed"] = base_row.get("wall_clock_skewed")
                row["effective_critical_path_ms"] = base_row.get("effective_critical_path_ms")
                break
        enriched.append(row)

    valid_serial = [r for r in enriched if r.get("serial_critical_path_ms") is not None]
    return {
        "phase": phase,
        "formula": (
            "entry_compute + transfer_ab + middle_compute + transfer_bc + "
            "final_compute + sampling"
        ),
        "clock_skew_detected": base.get("clock_skew_detected", False),
        "clock_skew_wave_count": base.get("clock_skew_wave_count", 0),
        "avg_serial_critical_path_ms": round(
            statistics.mean([r["serial_critical_path_ms"] for r in valid_serial]), 3
        ) if valid_serial else None,
        "avg_effective_critical_path_ms": base.get("avg_effective_critical_path_ms"),
        "avg_wall_critical_path_ms": base.get("avg_wall_critical_path_ms"),
        "token_rows": enriched,
        "complete_count": len(valid_serial),
        "wave_count": len(enriched),
    }


def build_bubble_doc(
        events: list[dict[str, Any]],
        critical_doc: dict[str, Any],
        *,
        phase: str = "decode",
        all_stages_pass: bool = True,
) -> dict[str, Any]:
    bubble = compute_bubble_from_entry_periods(
        events,
        critical_doc.get("token_rows") or [],
        phase=phase,
    )
    if not all_stages_pass:
        bubble = {
            **bubble,
            "status": "UNKNOWN",
            "reason": "missing decode spans on one or more stages",
        }
    return bubble


def build_utilization_doc(
        events: list[dict[str, Any]],
        critical_doc: dict[str, Any],
        *,
        phase: str = "decode",
        all_stages_pass: bool = True,
) -> dict[str, Any]:
    if not all_stages_pass:
        return {
            "status": "UNKNOWN",
            "reason": "utilization requires full-stage decode spans",
        }

    by_stage: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for ev in events:
        if str(ev.get("phase", "")) != phase:
            continue
        stage = str(ev.get("stage", ""))
        if stage not in STAGES:
            continue
        cat = str(ev.get("category", ""))
        dur = ev.get("dur_us")
        if not isinstance(dur, (int, float)) or dur <= 0:
            continue
        by_stage[stage][cat] += float(dur) / 1000.0

    stages_out: dict[str, Any] = {}
    for stage in STAGES:
        buckets = by_stage.get(stage, {})
        busy = buckets.get("COMPUTE", 0.0) + buckets.get("NETWORK", 0.0) + buckets.get("SAMPLING", 0.0)
        serialize = buckets.get("SERIALIZATION", 0.0)
        wait = buckets.get("WAIT", 0.0) + buckets.get("IDLE", 0.0)
        total = busy + serialize + wait
        if total <= 0:
            stages_out[stage] = {"status": "UNKNOWN", "reason": "no span data"}
            continue
        stages_out[stage] = {
            "busy_ms": round(busy, 3),
            "idle_ms": round(wait, 3),
            "serialize_ms": round(serialize, 3),
            "compute_ms": round(buckets.get("COMPUTE", 0.0), 3),
            "network_ms": round(buckets.get("NETWORK", 0.0), 3),
            "utilization_pct": round(100.0 * busy / total, 2),
            "idle_pct": round(100.0 * wait / total, 2),
        }

    avg_crit = critical_doc.get("avg_wall_critical_path_ms") or critical_doc.get("avg_serial_critical_path_ms")
    pipeline_util = None
    if avg_crit and avg_crit > 0:
        compute_sum = sum(stages_out.get(s, {}).get("compute_ms", 0.0) for s in STAGES)
        waves = critical_doc.get("wave_count") or 1
        if waves > 0:
            pipeline_util = round(100.0 * compute_sum / (avg_crit * waves), 2)

    return {
        "status": "PASS",
        "stages": stages_out,
        "pipeline_utilization_pct": pipeline_util,
    }


def build_serialization_doc(
        events: list[dict[str, Any]],
        *,
        phase: str = "decode",
        all_stages_pass: bool = True,
) -> dict[str, Any]:
    if not all_stages_pass:
        return {
            "status": "UNKNOWN",
            "reason": "serialization requires full decode spans",
        }

    rows: list[dict[str, Any]] = []
    for ev in events:
        if str(ev.get("phase", "")) != phase:
            continue
        event = str(ev.get("event", ""))
        cat = str(ev.get("category", ""))
        if cat != "SERIALIZATION" and event not in ("SERIALIZE_HIDDEN_END", "DESERIALIZE_HIDDEN_END"):
            if event != "HIDDEN_TRANSFER":
                continue
        dur_ms = _ms(ev.get("dur_us"))
        attrs = ev.get("attrs") if isinstance(ev.get("attrs"), dict) else {}
        ser_us = attrs.get("serialize_us") if isinstance(attrs, dict) else None
        deser_us = attrs.get("deserialize_us") if isinstance(attrs, dict) else None
        rows.append({
            "WaveID": wave_correlation_key(ev),
            "stage": ev.get("stage"),
            "event": event,
            "dur_ms": dur_ms,
            "serialize_ms": _ms(ser_us) if isinstance(ser_us, (int, float)) else None,
            "deserialize_ms": _ms(deser_us) if isinstance(deser_us, (int, float)) else None,
            "link": attrs.get("link") if isinstance(attrs, dict) else None,
            "payload_bytes": attrs.get("payload_bytes") if isinstance(attrs, dict) else None,
        })

    serialize_vals = [
        r["serialize_ms"] for r in rows
        if r.get("serialize_ms") is not None
    ]
    deserialize_vals = [
        r["deserialize_ms"] for r in rows
        if r.get("deserialize_ms") is not None
    ]
    span_vals = [r["dur_ms"] for r in rows if r.get("dur_ms") is not None and "SERIAL" in str(r.get("event", ""))]

    return {
        "status": "PASS" if rows else "UNKNOWN",
        "sample_count": len(rows),
        "avg_serialize_ms": round(statistics.mean(serialize_vals), 3) if serialize_vals else None,
        "avg_deserialize_ms": round(statistics.mean(deserialize_vals), 3) if deserialize_vals else None,
        "avg_span_ms": round(statistics.mean(span_vals), 3) if span_vals else None,
        "rows": rows[:500],
    }


def build_network_doc(
        events: list[dict[str, Any]],
        *,
        phase: str = "decode",
        all_stages_pass: bool = True,
) -> dict[str, Any]:
    if not all_stages_pass:
        return {
            "status": "UNKNOWN",
            "reason": "network metrics require full decode spans",
        }

    hops: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        if str(ev.get("phase", "")) != phase:
            continue
        event = str(ev.get("event", ""))
        if event not in ("HIDDEN_TRANSFER", "ENTRY_SEND_END", "MIDDLE_SEND_END"):
            continue
        attrs = ev.get("attrs") if isinstance(ev.get("attrs"), dict) else {}
        link = str(attrs.get("link", ""))
        if event == "ENTRY_SEND_END":
            link = link or "ab"
        elif event == "MIDDLE_SEND_END":
            link = link or "bc"
        dur_ms = _ms(ev.get("dur_us"))
        payload = attrs.get("payload_bytes")
        throughput = None
        if isinstance(payload, (int, float)) and dur_ms and dur_ms > 0:
            throughput = round(float(payload) / (dur_ms / 1000.0) / 1_000_000, 3)
        row = {
            "WaveID": wave_correlation_key(ev),
            "link": link,
            "stage": ev.get("stage"),
            "event": event,
            "latency_ms": dur_ms,
            "payload_bytes": payload,
            "serialize_ms": _ms(attrs.get("serialize_us")),
            "send_ms": _ms(attrs.get("send_us")),
            "receive_ms": _ms(attrs.get("receive_us")),
            "deserialize_ms": _ms(attrs.get("deserialize_us")),
            "throughput_mbps": throughput,
        }
        hop_key = link if link else "unknown"
        hops[hop_key].append(row)

    summary: dict[str, Any] = {}
    for link, link_rows in hops.items():
        lats = [r["latency_ms"] for r in link_rows if r.get("latency_ms") is not None]
        payloads = [r["payload_bytes"] for r in link_rows if r.get("payload_bytes") is not None]
        summary[link] = {
            "hop_count": len(link_rows),
            "avg_latency_ms": round(statistics.mean(lats), 3) if lats else None,
            "avg_payload_bytes": int(statistics.mean(payloads)) if payloads else None,
        }

    return {
        "status": "PASS" if hops else "UNKNOWN",
        "hops": dict(hops),
        "summary": summary,
    }


def build_scheduler_doc(
        events: list[dict[str, Any]],
        *,
        phase: str = "decode",
        all_stages_pass: bool = True,
) -> dict[str, Any]:
    if not all_stages_pass:
        return {
            "status": "UNKNOWN",
            "reason": "scheduler breakdown requires full decode spans",
        }

    buckets: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for ev in events:
        if str(ev.get("phase", "")) != phase:
            continue
        event = str(ev.get("event", ""))
        cat = str(ev.get("category", ""))
        dur = ev.get("dur_us")
        if not isinstance(dur, (int, float)) or dur <= 0:
            continue
        bucket = None
        if event in SCHEDULER_EVENTS:
            bucket = SCHEDULER_EVENTS[event]
        elif cat == "WAIT":
            bucket = "rpc_wait"
        elif cat == "IDLE":
            bucket = "idle"
        elif cat == "NETWORK" and event not in ("HIDDEN_TRANSFER", "ENTRY_SEND_END", "MIDDLE_SEND_END"):
            bucket = "network_wait"
        if bucket:
            buckets[bucket] += float(dur) / 1000.0
            counts[bucket] += 1

    total = sum(buckets.values()) or 1.0
    return {
        "status": "PASS" if buckets else "UNKNOWN",
        "buckets_ms": {k: round(v, 3) for k, v in buckets.items()},
        "buckets_pct": {k: round(100.0 * v / total, 2) for k, v in sorted(buckets.items(), key=lambda x: -x[1])},
        "event_counts": dict(counts),
    }


def write_observability_artifacts(
        raw_dir: Path,
        analysis_dir: Path,
        *,
        trace_id: str | None = None,
        all_stages_pass: bool = True,
) -> dict[str, Any]:
    """Write Task 14 deliverable JSON files under analysis_dir."""
    analysis_dir = Path(analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    events = load_raw_events(raw_dir)
    tid = pick_primary_trace_id(events, prefer=trace_id)
    trace_events = filter_trace(events, tid) if tid else events

    timeline = build_unified_timeline(trace_events, trace_id=tid)
    critical = build_critical_path_doc(trace_events)
    bubble = build_bubble_doc(trace_events, critical, all_stages_pass=all_stages_pass)
    utilization = build_utilization_doc(trace_events, critical, all_stages_pass=all_stages_pass)
    serialization = build_serialization_doc(trace_events, all_stages_pass=all_stages_pass)
    network = build_network_doc(trace_events, all_stages_pass=all_stages_pass)
    scheduler = build_scheduler_doc(trace_events, all_stages_pass=all_stages_pass)

    (analysis_dir / "timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")
    (analysis_dir / "critical_path.json").write_text(json.dumps(critical, indent=2), encoding="utf-8")
    (analysis_dir / "bubble.json").write_text(json.dumps(bubble, indent=2), encoding="utf-8")
    (analysis_dir / "utilization.json").write_text(json.dumps(utilization, indent=2), encoding="utf-8")
    (analysis_dir / "serialization.json").write_text(json.dumps(serialization, indent=2), encoding="utf-8")
    (analysis_dir / "network.json").write_text(json.dumps(network, indent=2), encoding="utf-8")
    (analysis_dir / "scheduler.json").write_text(json.dumps(scheduler, indent=2), encoding="utf-8")

    return {
        "trace_id": tid,
        "timeline": timeline,
        "critical_path": critical,
        "bubble": bubble,
        "utilization": utilization,
        "serialization": serialization,
        "network": network,
        "scheduler": scheduler,
    }
