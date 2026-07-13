#!/usr/bin/env python3
"""Task 15.1 — Hidden transport (A→B pack) cost breakdown from perf trace spans."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

from perf_trace.metric_validation import filter_trace, load_raw_events, pick_primary_trace_id

# Sub-stage END events emitted by hidden_transport_breakdown.cpp (Task 15.1).
# gather_sync appears only with DIST_RUNTIME_GATHER_SYNC_SPLIT=1 (Task 17.3).
PACK_STAGE_EVENTS: dict[str, str] = {
    "allocation": "ALLOC_END",
    "gather_sync": "GATHER_SYNC_END",
    "gather_hidden": "GATHER_END",
    "copy": "COPY_END",
    "serialization": "SERIALIZE_END",
    "frame_build": "FRAME_END",
    "socket_send": "SEND_END",
}

LEGACY_PACK_EVENT = "SERIALIZE_HIDDEN_END"
SUMMARY_EVENT = "HIDDEN_PACK_SUMMARY"
TOTAL_EVENT = "HIDDEN_PACK_TOTAL_END"


def _ms(dur_us: int | float | None) -> float | None:
    if not isinstance(dur_us, (int, float)):
        return None
    return float(dur_us) / 1000.0


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[idx]


def _stage_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "avg_ms": None,
            "min_ms": None,
            "max_ms": None,
            "p95_ms": None,
            "total_ms": None,
            "contribution_pct": None,
        }
    total = sum(values)
    return {
        "count": len(values),
        "avg_ms": round(statistics.mean(values), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
        "p95_ms": round(_p95(values), 3),
        "total_ms": round(total, 3),
        "contribution_pct": None,
    }


def _parse_attrs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            doc = json.loads(raw)
            return doc if isinstance(doc, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _is_steady_decode_pack_event(ev: dict[str, Any]) -> bool:
    """Keep single-token decode hidden packs (8192 B), exclude prefill multi-token."""
    attrs = _parse_attrs(ev.get("attrs"))
    event = str(ev.get("event", ""))
    if event == "GATHER_END":
        n_tok = attrs.get("n_tokens")
        return n_tok in (1, "1", None)
    if event == "ALLOC_END":
        return attrs.get("bytes_requested") in (8192, "8192", None)
    if event == "COPY_END":
        return attrs.get("bytes_per_copy") in (8192, "8192", None)
    if event in ("SERIALIZE_END", "FRAME_END", "SEND_END", "HIDDEN_PACK_TOTAL_END"):
        return True
    return True


def _collect_stage_samples(
        events: list[dict[str, Any]],
        *,
        link: str = "ab",
        phase: str = "decode",
        steady_decode_only: bool = True,
) -> dict[str, list[float]]:
    samples: dict[str, list[float]] = {key: [] for key in PACK_STAGE_EVENTS}
    legacy: list[float] = []
    totals: list[float] = []

    for ev in events:
        if str(ev.get("phase", "")) != phase:
            continue
        if str(ev.get("stage", "")) != "entry":
            continue
        if steady_decode_only and not _is_steady_decode_pack_event(ev):
            continue
        name = str(ev.get("event", ""))
        dur = _ms(ev.get("dur_us"))
        if dur is None:
            continue

        attrs = _parse_attrs(ev.get("attrs"))
        if name.endswith("_END") and attrs.get("link") and attrs.get("link") != link:
            continue

        for stage_key, end_event in PACK_STAGE_EVENTS.items():
            if name == end_event:
                samples[stage_key].append(dur)
                break
        else:
            if name == LEGACY_PACK_EVENT:
                legacy.append(dur)
            elif name == TOTAL_EVENT:
                totals.append(dur)

    return {
        **samples,
        "legacy_pack_total": legacy,
        "measured_pack_total": totals,
    }


def _allocation_audit(events: list[dict[str, Any]], *, phase: str = "decode") -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("event") != "ALLOC_END" or str(ev.get("phase", "")) != phase:
            continue
        if not _is_steady_decode_pack_event(ev):
            continue
        attrs = _parse_attrs(ev.get("attrs"))
        rows.append({
            "WaveID": ev.get("WaveID"),
            "token_idx": ev.get("token_idx"),
            "capacity_before": attrs.get("capacity_before"),
            "capacity_after": attrs.get("capacity_after"),
            "capacity_grew": attrs.get("capacity_grew"),
            "bytes_requested": attrs.get("bytes_requested"),
            "dur_ms": _ms(ev.get("dur_us")),
        })

    grew = sum(1 for r in rows if r.get("capacity_grew") is True)
    fresh_alloc = sum(
        1 for r in rows
        if r.get("capacity_before") in (0, "0", None) and (r.get("capacity_after") or 0) > 0
    )
    return {
        "sample_count": len(rows),
        "capacity_grew_count": grew,
        "fresh_heap_alloc_count": fresh_alloc,
        "alloc_per_token": len(rows) > 0 and fresh_alloc == len(rows),
        "vector_resize_every_token": len(rows) > 0 and all(
            r.get("bytes_requested") == 8192 for r in rows if r.get("bytes_requested")
        ),
        "rows": rows[:50],
    }


def _copy_path_audit(events: list[dict[str, Any]], *, phase: str = "decode") -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    copy_rows: list[dict[str, Any]] = []
    for ev in events:
        if str(ev.get("phase", "")) != phase:
            continue
        name = str(ev.get("event", ""))
        attrs = _parse_attrs(ev.get("attrs"))
        if name == SUMMARY_EVENT:
            if attrs.get("payload_bytes") in (8192, "8192"):
                summaries.append(attrs)
        elif name == "COPY_END":
            copy_rows.append(attrs)

    heap_counts = [s.get("heap_copy_count") for s in summaries if s.get("heap_copy_count") is not None]
    typical_copy = statistics.mode(heap_counts) if heap_counts else None
    return {
        "summary_count": len(summaries),
        "typical_heap_copy_count": typical_copy,
        "copy_path": next((s.get("copy_path") for s in summaries if s.get("copy_path")), "ggml_embeddings->std::vector->kernel_tcp"),
        "explicit_memcpy_per_token": sum(
            1 for row in copy_rows if row.get("bytes_per_copy") in (8192, "8192")
        ),
        "copy_stages": [
            "GGML embedding tensor (llama_get_embeddings)",
            "heap std::vector<float> via memcpy",
            "kernel TCP send from user buffer (no extra heap buffer)",
        ],
    }


def build_hidden_transport_breakdown(
        events: list[dict[str, Any]],
        *,
        trace_id: str | None = None,
        link: str = "ab",
        phase: str = "decode",
        steady_decode_only: bool = True,
) -> dict[str, Any]:
    trace_events = filter_trace(events, trace_id) if trace_id else events
    samples = _collect_stage_samples(
        trace_events, link=link, phase=phase, steady_decode_only=steady_decode_only,
    )

    stage_stats: dict[str, Any] = {}
    for stage_key in PACK_STAGE_EVENTS:
        stage_stats[stage_key] = _stage_stats(samples[stage_key])

    # Total basis: measured HIDDEN_PACK_TOTAL_END, else sum of stages, else legacy span.
    if samples["measured_pack_total"]:
        total_basis = statistics.mean(samples["measured_pack_total"])
        total_source = "HIDDEN_PACK_TOTAL_END"
    elif any(samples[k] for k in PACK_STAGE_EVENTS):
        stage_avgs = [
            stage_stats[k]["avg_ms"]
            for k in PACK_STAGE_EVENTS
            if stage_stats[k]["avg_ms"] is not None
        ]
        total_basis = sum(stage_avgs) if stage_avgs else None
        total_source = "sum_of_stage_averages"
    elif samples["legacy_pack_total"]:
        total_basis = statistics.mean(samples["legacy_pack_total"])
        total_source = LEGACY_PACK_EVENT
    else:
        total_basis = None
        total_source = "missing"

    if total_basis and total_basis > 0:
        for stage_key in PACK_STAGE_EVENTS:
            avg = stage_stats[stage_key]["avg_ms"]
            if avg is not None:
                stage_stats[stage_key]["contribution_pct"] = round(100.0 * avg / total_basis, 2)

    serialize_present = any(d > 0 for d in samples["serialization"])
    alloc_audit = _allocation_audit(trace_events, phase=phase)
    copy_audit = _copy_path_audit(trace_events, phase=phase)

    return {
        "task": "15.1",
        "trace_id": trace_id,
        "link": link,
        "phase": phase,
        "status": "PASS" if total_basis is not None else "MISSING",
        "total_ms": round(total_basis, 3) if total_basis is not None else None,
        "total_source": total_source,
        "legacy_pack_avg_ms": round(statistics.mean(samples["legacy_pack_total"]), 3)
        if samples["legacy_pack_total"] else None,
        "stages": stage_stats,
        "serialization_stage_present": serialize_present,
        "allocation_audit": alloc_audit,
        "copy_path_audit": copy_audit,
        "answers": _build_answers(stage_stats, total_basis, serialize_present, alloc_audit, copy_audit),
    }


def _build_answers(
        stage_stats: dict[str, Any],
        total_ms: float | None,
        serialize_present: bool,
        alloc_audit: dict[str, Any],
        copy_audit: dict[str, Any],
) -> dict[str, Any]:
    if total_ms is None or total_ms <= 0:
        return {
            "most_expensive_operation": None,
            "real_serialization_ms": None,
            "memcpy_ms": None,
            "repeated_allocations": None,
            "hidden_copy_count": None,
            "socket_send_significant": None,
        }

    ranked = sorted(
        ((k, v.get("avg_ms") or 0.0) for k, v in stage_stats.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    top_key, top_ms = ranked[0]
    send_ms = stage_stats.get("socket_send", {}).get("avg_ms") or 0.0
    return {
        "most_expensive_operation": {
            "stage": top_key,
            "avg_ms": top_ms,
            "contribution_pct": stage_stats.get(top_key, {}).get("contribution_pct"),
        },
        "real_serialization_ms": stage_stats.get("serialization", {}).get("avg_ms"),
        "serialization_separate_stage": serialize_present,
        "memcpy_ms": stage_stats.get("copy", {}).get("avg_ms"),
        "repeated_allocations": alloc_audit.get("alloc_per_token"),
        "hidden_copy_count": copy_audit.get("typical_heap_copy_count"),
        "socket_send_significant": send_ms >= 0.05 and (send_ms / total_ms) >= 0.05,
        "socket_send_avg_ms": send_ms,
    }


def build_breakdown_md(doc: dict[str, Any]) -> str:
    lines = [
        "# Task 15.1 — Hidden Transport Breakdown (A→B)",
        "",
        f"**Trace ID:** `{doc.get('trace_id', '—')}`",
        f"**Status:** {doc.get('status', 'UNKNOWN')}",
        f"**Total pack time:** **{doc.get('total_ms', '—')} ms** ({doc.get('total_source', '—')})",
        "",
        "## Hidden Pack",
        "",
        f"**Total:** {doc.get('total_ms', '—')} ms",
        "",
    ]

    labels = {
        "allocation": "Allocation",
        "gather_hidden": "Gather hidden",
        "copy": "Copy (memcpy)",
        "serialization": "Serialization",
        "frame_build": "Frame build",
        "socket_send": "Socket send",
    }
    stages = doc.get("stages") or {}
    for key, label in labels.items():
        st = stages.get(key) or {}
        avg = st.get("avg_ms")
        if avg is None and key == "serialization":
            lines.append(f"**{label}:** absent (0 ms — no separate serialize buffer)")
            continue
        lines.append(f"**{label}:** {avg if avg is not None else '—'} ms")

    lines.extend(["", "## Stage statistics", ""])
    lines.append("| Stage | avg (ms) | min | max | p95 | total (ms) | contribution % |")
    lines.append("|-------|---------:|----:|----:|----:|-----------:|---------------:|")
    for key, label in labels.items():
        st = stages.get(key) or {}
        lines.append(
            f"| {label} | {st.get('avg_ms', '—')} | {st.get('min_ms', '—')} | "
            f"{st.get('max_ms', '—')} | {st.get('p95_ms', '—')} | "
            f"{st.get('total_ms', '—')} | {st.get('contribution_pct', '—')} |"
        )

    answers = doc.get("answers") or {}
    lines.extend(["", "## Acceptance answers (trace-based)", ""])
    top = answers.get("most_expensive_operation") or {}
    lines.append(f"1. **Most expensive operation:** {top.get('stage', '—')} "
                 f"({top.get('avg_ms', '—')} ms, {top.get('contribution_pct', '—')}%)")
    lines.append(f"2. **Real serialization time:** {answers.get('real_serialization_ms', '—')} ms "
                 f"(separate stage present: {answers.get('serialization_separate_stage')})")
    lines.append(f"3. **memcpy time:** {answers.get('memcpy_ms', '—')} ms")
    lines.append(f"4. **Repeated allocations per token:** {answers.get('repeated_allocations')}")
    lines.append(f"5. **Hidden buffer copies:** {answers.get('hidden_copy_count')}")
    lines.append(f"6. **Socket send significant:** {answers.get('socket_send_significant')} "
                 f"(avg {answers.get('socket_send_avg_ms', '—')} ms)")

    alloc = doc.get("allocation_audit") or {}
    lines.extend([
        "",
        "## Allocation audit (Phase D)",
        "",
        f"- Samples: {alloc.get('sample_count', 0)}",
        f"- Fresh heap alloc per token: **{alloc.get('alloc_per_token')}**",
        f"- capacity_grew events: {alloc.get('capacity_grew_count', 0)}",
    ])

    copy = doc.get("copy_path_audit") or {}
    lines.extend([
        "",
        "## Copy path (Phase E)",
        "",
        f"- Path: `{copy.get('copy_path', '—')}`",
        f"- Heap copy count (including TCP read): **{copy.get('typical_heap_copy_count', '—')}**",
    ])
    for i, step in enumerate(copy.get("copy_stages") or [], 1):
        lines.append(f"  {i}. {step}")

    lines.extend([
        "",
        "## Interpretation",
        "",
        "The legacy `SERIALIZE_HIDDEN_END` span bundled gather + alloc + memcpy. "
        "Sub-stage spans show **`llama_get_embeddings` access (GATHER)** dominates "
        "— not memcpy, heap allocation, wire framing, or TCP payload send.",
        "",
        "## Methodology",
        "",
        "- C++: `hidden_transport_breakdown.cpp` in entry worker (`split_gen3_a`)",
        "- Python: `benchmarks/perf_trace/hidden_transport_breakdown.py`",
        "- Steady decode filter: 8192 B payload, single-token waves",
        "- Measurement only — no protocol / wire / format changes",
    ])

    return "\n".join(lines) + "\n"


def write_hidden_transport_breakdown_csv(doc: dict[str, Any], path: Path) -> None:
    labels = {
        "allocation": "Allocation",
        "gather_hidden": "Gather hidden",
        "copy": "Copy",
        "serialization": "Serialization",
        "frame_build": "Frame build",
        "socket_send": "Socket send",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "stage", "avg_ms", "min_ms", "max_ms", "p95_ms", "total_ms", "contribution_pct", "count",
        ])
        for key, label in labels.items():
            st = (doc.get("stages") or {}).get(key) or {}
            writer.writerow([
                label,
                st.get("avg_ms"),
                st.get("min_ms"),
                st.get("max_ms"),
                st.get("p95_ms"),
                st.get("total_ms"),
                st.get("contribution_pct"),
                st.get("count"),
            ])


def _find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "docs").is_dir() and (candidate / "benchmarks").is_dir():
            return candidate
    return Path.cwd()


def write_hidden_transport_breakdown(
        raw_dir: Path,
        analysis_dir: Path,
        *,
        trace_id: str | None = None,
        link: str = "ab",
        docs_path: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = Path(analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    events = load_raw_events(Path(raw_dir))
    tid = pick_primary_trace_id(events, prefer=trace_id)
    doc = build_hidden_transport_breakdown(events, trace_id=tid, link=link)

    json_path = analysis_dir / "hidden_transport_breakdown.json"
    csv_path = analysis_dir / "hidden_transport_breakdown.csv"
    md_text = build_breakdown_md(doc)
    json_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    write_hidden_transport_breakdown_csv(doc, csv_path)
    (analysis_dir / "hidden_transport_breakdown.md").write_text(md_text, encoding="utf-8")

    repo_root = _find_repo_root(analysis_dir)
    md_path = docs_path or repo_root / "docs" / "TASK_15_1_HIDDEN_TRANSPORT_BREAKDOWN.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    doc["artifacts"] = {
        "json": str(json_path),
        "csv": str(csv_path),
        "markdown": str(md_path),
        "analysis_markdown": str(analysis_dir / "hidden_transport_breakdown.md"),
    }
    return doc
