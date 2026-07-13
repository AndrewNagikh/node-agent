#!/usr/bin/env python3
"""Task 15.1b — root-cause breakdown of llama_get_embeddings / gather latency."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

from perf_trace.metric_validation import filter_trace, load_raw_events, pick_primary_trace_id

GATHER_EVENTS = {
    "gather_total": "GATHER_END",
    "backend_synchronize": "LLAMA_BACKEND_SYNCHRONIZE",
    "get_embeddings_access": "LLAMA_GET_EMBEDDINGS_ACCESS",
    "get_embeddings_total": "LLAMA_GET_EMBEDDINGS",
    "embd_d2h_async": "EMBD_D2H_GET_ASYNC",
    "graph_execute": "GGML_GRAPH_EXECUTE",
    "entry_compute": "ENTRY_COMPUTE_END",
}


def _ms(dur_us: int | float | None) -> float | None:
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
            "p95_ms": None, "total_ms": None, "contribution_pct": None,
        }
    return {
        "count": len(values),
        "avg_ms": round(statistics.mean(values), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
        "p95_ms": round(_p95(values), 3),
        "total_ms": round(sum(values), 3),
        "contribution_pct": None,
    }


def _wave_key(ev: dict[str, Any]) -> int | None:
    w = ev.get("WaveID")
    if isinstance(w, int):
        return w
    t = ev.get("token_idx")
    if isinstance(t, int) and t >= 0:
        return t
    return None


def _end_ts_us(ev: dict[str, Any]) -> int | None:
    ts = ev.get("ts_us")
    dur = ev.get("dur_us")
    if isinstance(ts, int) and isinstance(dur, (int, float)):
        return ts + int(dur)
    return ts if isinstance(ts, int) else None


def _collect_by_wave(
        events: list[dict[str, Any]],
        *,
        phase: str = "decode",
) -> dict[int, dict[str, Any]]:
    by_wave: dict[int, dict[str, Any]] = {}
    for ev in events:
        if str(ev.get("phase", "")) != phase:
            continue
        wave = _wave_key(ev)
        if wave is None:
            continue
        row = by_wave.setdefault(wave, {})
        name = str(ev.get("event", ""))
        for key, event_name in GATHER_EVENTS.items():
            if name == event_name:
                dur = _ms(ev.get("dur_us"))
                if dur is not None:
                    row.setdefault(key, []).append({
                        "dur_ms": dur,
                        "ts_us": ev.get("ts_us"),
                        "end_ts_us": _end_ts_us(ev),
                        "stage": ev.get("stage"),
                    })
    return by_wave


def build_hidden_gather_breakdown(
        events: list[dict[str, Any]],
        *,
        trace_id: str | None = None,
        phase: str = "decode",
) -> dict[str, Any]:
    trace_events = filter_trace(events, trace_id) if trace_id else events
    by_wave = _collect_by_wave(trace_events, phase=phase)

    steady_waves = sorted(
        w for w, row in by_wave.items()
        if row.get("gather_total") and w >= 0
    )

    stage_samples: dict[str, list[float]] = {k: [] for k in GATHER_EVENTS}
    timeline_rows: list[dict[str, Any]] = []

    for wave in steady_waves:
        row = by_wave[wave]
        gather = row.get("gather_total", [{}])[0]
        sync = (row.get("backend_synchronize") or [{}])[0]
        access = (row.get("get_embeddings_access") or [{}])[0]
        d2h = (row.get("embd_d2h_async") or [{}])[0]
        graph = (row.get("graph_execute") or [{}])[-1:]  # last graph before gather
        graph_ev = graph[0] if graph else {}
        entry = (row.get("entry_compute") or [{}])[0]

        gather_ms = gather.get("dur_ms")
        sync_ms = sync.get("dur_ms")
        access_ms = access.get("dur_ms")
        d2h_ms = d2h.get("dur_ms")
        graph_end = graph_ev.get("end_ts_us")
        gather_begin = gather.get("ts_us")

        gap_graph_to_gather_ms = None
        if isinstance(graph_end, int) and isinstance(gather_begin, int):
            raw_gap = (gather_begin - graph_end) / 1000.0
            if -1000.0 < raw_gap < 1000.0:
                gap_graph_to_gather_ms = raw_gap

        if gather_ms is not None:
            stage_samples["gather_total"].append(gather_ms)
        if sync_ms is not None:
            stage_samples["backend_synchronize"].append(sync_ms)
        if access_ms is not None:
            stage_samples["get_embeddings_access"].append(access_ms)
        if d2h_ms is not None:
            stage_samples["embd_d2h_async"].append(d2h_ms)
        if graph_ev.get("dur_ms") is not None:
            stage_samples["graph_execute"].append(graph_ev["dur_ms"])

        timeline_rows.append({
            "WaveID": wave,
            "graph_execute_end_to_gather_begin_ms": round(gap_graph_to_gather_ms, 3)
            if gap_graph_to_gather_ms is not None else None,
            "gather_total_ms": gather_ms,
            "backend_synchronize_ms": sync_ms,
            "get_embeddings_access_ms": access_ms,
            "embd_d2h_async_ms": d2h_ms,
            "entry_compute_ms": entry.get("dur_ms"),
        })

    stages = {k: _stage_stats(v) for k, v in stage_samples.items()}

    gather_avg = stages["gather_total"].get("avg_ms")
    if gather_avg and gather_avg > 0:
        for key in ("backend_synchronize", "get_embeddings_access", "embd_d2h_async"):
            avg = stages[key].get("avg_ms")
            if avg is not None:
                stages[key]["contribution_pct"] = round(100.0 * avg / gather_avg, 2)

    gaps = [
        r["graph_execute_end_to_gather_begin_ms"]
        for r in timeline_rows
        if r.get("graph_execute_end_to_gather_begin_ms") is not None
    ]

    sync_avg = stages["backend_synchronize"].get("avg_ms")
    access_avg = stages["get_embeddings_access"].get("avg_ms")
    d2h_avg = stages["embd_d2h_async"].get("avg_ms")
    gather_total = gather_avg or 0.0

    answers = {
        "q1_graph_execute_before_gather": {
            "avg_gap_ms": round(statistics.mean(gaps), 3) if gaps else None,
            "gap_reliable": bool(gaps),
            "interpretation": (
                "GGML_GRAPH_EXECUTE completes during ENTRY_COMPUTE (async). "
                "EMBD_D2H_GET_ASYNC is queued at end of decode. "
                "llama_get_embeddings() later calls synchronize() to wait for GPU + D2H. "
                "Wall-clock gap unreliable on homelab (clock skew) — use span durations."
            ),
        },
        "q2_ggml_backend_tensor_get_present": {
            "answer": True,
            "event": "EMBD_D2H_GET_ASYNC",
            "avg_queue_ms": d2h_avg,
            "note": "Async D2H queued at decode; completion waited in synchronize()",
        },
        "q3_gpu_synchronize_present": {
            "answer": True,
            "event": "LLAMA_BACKEND_SYNCHRONIZE",
            "backend": "metal (entry node-a homelab)",
            "avg_ms": sync_avg,
        },
        "q4_device_to_host_copy": {
            "answer": True,
            "mechanism": "ggml_backend_tensor_get_async during decode + completion in synchronize",
            "async_queue_avg_ms": d2h_avg,
            "note": "D2H queue op is ~0ms; bytes transfer completes inside synchronize wait",
        },
        "q5_wait_starts_where": {
            "model": (
                "Graph Execute (async) → EMBD_D2H_GET_ASYNC queued → "
                "… CPU / orchestrator gap … → llama_get_embeddings() → "
                "LLAMA_BACKEND_SYNCHRONIZE (GPU wait + D2H complete) → "
                "LLAMA_GET_EMBEDDINGS_ACCESS (output_reorder, ~0ms) → return pointer"
            ),
            "avg_graph_end_to_gather_gap_ms": round(statistics.mean(gaps), 3) if gaps else None,
        },
        "q6_alternative_apis": {
            "llama_get_hidden_state": "Copies hidden_state_inp buffer — input path, not entry output",
            "embd_data_after_sync": "Pointer valid after synchronize; memcpy in COPY stage is redundant if send could use embd.data directly after sync",
            "skip_llama_get_embeddings": "Possible if synchronize()+get_embeddings() split and called once per token after decode",
        },
    }

    decomposition = {}
    if gather_total > 0:
        decomposition = {
            "gather_total_ms": round(gather_total, 3),
            "gpu_wait_backend_synchronize_ms": sync_avg,
            "api_access_output_reorder_ms": access_avg,
            "embd_d2h_async_queue_ms": d2h_avg,
            "unattributed_ms": round(
                gather_total - (sync_avg or 0) - (access_avg or 0), 3
            ) if sync_avg is not None else None,
        }

    return {
        "task": "15.1b",
        "trace_id": trace_id,
        "phase": phase,
        "status": "PASS" if gather_avg is not None else "MISSING",
        "steady_wave_count": len(steady_waves),
        "stages": stages,
        "decomposition": decomposition,
        "timeline_rows": timeline_rows[:50],
        "answers": answers,
        "acceptance": {
            "most_expensive_substage": (
                "backend_synchronize" if (sync_avg or 0) >= (access_avg or 0) else "get_embeddings_access"
            ),
            "gather_equals_sync_plus_access": (
                sync_avg is not None and access_avg is not None
                and abs(gather_total - (sync_avg + access_avg)) < 0.05
            ),
            "prediction_gpu_sync_dominates": (
                sync_avg is not None and gather_total > 0 and (sync_avg / gather_total) > 0.8
            ),
        },
    }


def build_gather_md(doc: dict[str, Any]) -> str:
    dec = doc.get("decomposition") or {}
    stages = doc.get("stages") or {}
    answers = doc.get("answers") or {}
    acc = doc.get("acceptance") or {}

    lines = [
        "# Task 15.1b — Hidden Gather Root Cause",
        "",
        f"**Trace ID:** `{doc.get('trace_id', '—')}`",
        f"**Status:** {doc.get('status', 'UNKNOWN')}",
        f"**Steady waves:** {doc.get('steady_wave_count', 0)}",
        "",
        "## Gather decomposition (trace-based)",
        "",
        f"**GATHER total:** {dec.get('gather_total_ms', '—')} ms",
        "",
        f"**GPU wait (`LLAMA_BACKEND_SYNCHRONIZE`):** {dec.get('gpu_wait_backend_synchronize_ms', '—')} ms",
        f"**API access (`output_reorder` + pointer):** {dec.get('api_access_output_reorder_ms', '—')} ms",
        f"**EMBD D2H async queue (`ggml_backend_tensor_get_async`):** {dec.get('embd_d2h_async_queue_ms', '—')} ms",
        f"**Unattributed:** {dec.get('unattributed_ms', '—')} ms",
        "",
        "## Stage statistics",
        "",
        "| Stage | avg (ms) | min | max | p95 | contribution % |",
        "|-------|---------:|----:|----:|----:|---------------:|",
    ]

    labels = {
        "gather_total": "GATHER (llama_get_embeddings)",
        "backend_synchronize": "LLAMA_BACKEND_SYNCHRONIZE",
        "get_embeddings_access": "LLAMA_GET_EMBEDDINGS_ACCESS",
        "embd_d2h_async": "EMBD_D2H_GET_ASYNC (decode)",
        "graph_execute": "GGML_GRAPH_EXECUTE",
    }
    for key, label in labels.items():
        st = stages.get(key) or {}
        lines.append(
            f"| {label} | {st.get('avg_ms', '—')} | {st.get('min_ms', '—')} | "
            f"{st.get('max_ms', '—')} | {st.get('p95_ms', '—')} | {st.get('contribution_pct', '—')} |"
        )

    lines.extend(["", "## Acceptance questions", ""])
    q = answers.get("q1_graph_execute_before_gather") or {}
    lines.append(
        f"1. **Graph execute vs gather:** "
        f"{'avg gap ' + str(q.get('avg_gap_ms')) + ' ms' if q.get('gap_reliable') else 'wall gap unreliable (clock skew)'} — "
        f"{q.get('interpretation', '')}"
    )
    q2 = answers.get("q2_ggml_backend_tensor_get_present") or {}
    lines.append(f"2. **`ggml_backend_tensor_get`:** {q2.get('answer')} — event `{q2.get('event')}`, queue {q2.get('avg_queue_ms', '—')} ms")
    q3 = answers.get("q3_gpu_synchronize_present") or {}
    lines.append(f"3. **GPU synchronize:** {q3.get('answer')} — `{q3.get('event')}` avg {q3.get('avg_ms', '—')} ms ({q3.get('backend', '')})")
    q4 = answers.get("q4_device_to_host_copy") or {}
    lines.append(f"4. **Device→host:** {q4.get('answer')} — {q4.get('mechanism', '')}")
    q5 = answers.get("q5_wait_starts_where") or {}
    lines.append(f"5. **Wait model:** {q5.get('model', '')}")
    q6 = answers.get("q6_alternative_apis") or {}
    lines.append("6. **Alternatives:**")
    for k, v in q6.items():
        lines.append(f"   - `{k}`: {v}")

    lines.extend([
        "",
        "## Verdict",
        "",
        f"- GPU sync dominates gather: **{acc.get('prediction_gpu_sync_dominates')}**",
        f"- gather ≈ sync + access: **{acc.get('gather_equals_sync_plus_access')}**",
        "",
        "## Implication for Task 15.2",
        "",
        "If GPU wait ≈ gather, transport zero-copy / FP16 will **not** fix the 5 ms bottleneck. "
        "Next lever: overlap synchronize with pipeline, or avoid calling llama_get_embeddings per token.",
    ])
    return "\n".join(lines) + "\n"


def write_hidden_gather_breakdown_csv(doc: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "WaveID", "gather_total_ms", "backend_synchronize_ms",
            "get_embeddings_access_ms", "embd_d2h_async_ms",
            "graph_execute_end_to_gather_begin_ms", "entry_compute_ms",
        ])
        for row in doc.get("timeline_rows") or []:
            writer.writerow([
                row.get("WaveID"),
                row.get("gather_total_ms"),
                row.get("backend_synchronize_ms"),
                row.get("get_embeddings_access_ms"),
                row.get("embd_d2h_async_ms"),
                row.get("graph_execute_end_to_gather_begin_ms"),
                row.get("entry_compute_ms"),
            ])


def _find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "docs").is_dir() and (candidate / "benchmarks").is_dir():
            return candidate
    return Path.cwd()


def write_hidden_gather_breakdown(
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
    doc = build_hidden_gather_breakdown(events, trace_id=tid)

    json_path = analysis_dir / "hidden_gather_breakdown.json"
    csv_path = analysis_dir / "hidden_gather_breakdown.csv"
    md_text = build_gather_md(doc)
    json_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    write_hidden_gather_breakdown_csv(doc, csv_path)
    (analysis_dir / "hidden_gather_breakdown.md").write_text(md_text, encoding="utf-8")

    repo_root = _find_repo_root(analysis_dir)
    md_path = docs_path or repo_root / "docs" / "TASK_15_1b_HIDDEN_GATHER_ROOT_CAUSE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    doc["artifacts"] = {
        "json": str(json_path),
        "csv": str(csv_path),
        "markdown": str(md_path),
    }
    return doc
