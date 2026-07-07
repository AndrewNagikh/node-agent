#!/usr/bin/env python3
"""Task 12.8 — decode bottleneck rollup and budget PASS/WARN/FAIL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

BudgetStatus = Literal["PASS", "WARN", "FAIL", "SKIP"]

BUDGET_TARGETS: dict[str, dict[str, Any]] = {
    "session_create_ms": {
        "label": "Warm session create",
        "target": 1000.0,
        "unit": "ms",
        "mode": "lte",
        "phase": "session",
    },
    "ttft_ms": {
        "label": "TTFT (warm)",
        "target": 2000.0,
        "unit": "ms",
        "mode": "lte",
        "phase": "ttft",
    },
    "decode_ms_per_token": {
        "label": "Decode ms/token",
        "target": None,
        "unit": "ms",
        "mode": "lte",
        "phase": "decode",
        "note": "Compare vs mono in mono_compare (12.11)",
    },
    "decode_overhead_pct": {
        "label": "Decode overhead vs mono",
        "target": 20.0,
        "unit": "%",
        "mode": "lte",
        "phase": "decode",
        "optional": True,
    },
    "hidden_transfer_ms_per_hop": {
        "label": "Hidden transfer",
        "target": 1.0,
        "unit": "ms",
        "mode": "lte",
        "phase": "decode",
    },
    "serialize_ms_per_hop": {
        "label": "Serialization",
        "target": 0.5,
        "unit": "ms",
        "mode": "lte",
        "phase": "decode",
    },
    "scheduler_wait_ms_per_token": {
        "label": "Scheduler wait",
        "target": 1.0,
        "unit": "ms",
        "mode": "lte",
        "phase": "decode",
    },
    "worker_idle_pct": {
        "label": "Worker idle",
        "target": 10.0,
        "unit": "%",
        "mode": "lte",
        "phase": "decode",
    },
    "pipeline_utilization_pct": {
        "label": "Pipeline utilization",
        "target": 90.0,
        "unit": "%",
        "mode": "gte",
        "phase": "decode",
    },
    "install_reuse_pct": {
        "label": "Install reuse at READY",
        "target": 100.0,
        "unit": "%",
        "mode": "gte",
        "phase": "install",
    },
    "unknown_pct": {
        "label": "Unknown time bucket",
        "target": 5.0,
        "unit": "%",
        "mode": "lte",
        "phase": "decode",
    },
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def budget_status(
        value: float | None,
        target: float | None,
        mode: str = "lte",
        *,
        warn_ratio: float = 2.0,
) -> BudgetStatus:
    if value is None or target is None:
        return "SKIP"
    if mode == "lte":
        if value <= target:
            return "PASS"
        if value <= target * warn_ratio:
            return "WARN"
        return "FAIL"
    if mode == "gte":
        if value >= target:
            return "PASS"
        if value >= target / warn_ratio:
            return "WARN"
        return "FAIL"
    return "SKIP"


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def token_decode_metrics(tokens: list[dict[str, Any]]) -> dict[str, float | None]:
    if not tokens:
        return {}
    totals = [float(t.get("total_ms") or 0.0) for t in tokens]
    compute = [
        float(t.get("entry_compute_ms") or 0.0)
        + float(t.get("middle_compute_ms") or 0.0)
        + float(t.get("final_compute_ms") or 0.0)
        for t in tokens
    ]
    network = [float(t.get("network_ms") or 0.0) for t in tokens]
    serialize = [float(t.get("serialize_ms") or 0.0) for t in tokens]
    sampling = [float(t.get("sampling_ms") or 0.0) for t in tokens]
    wait = [
        float(t.get("entry_wait_ms") or 0.0)
        + float(t.get("middle_wait_ms") or 0.0)
        + float(t.get("final_wait_ms") or 0.0)
        for t in tokens
    ]

    avg_total = _avg(totals) or 0.001
    avg_compute = _avg(compute) or 0.0
    avg_network = _avg(network) or 0.0
    avg_serialize = _avg(serialize) or 0.0
    avg_sampling = _avg(sampling) or 0.0
    avg_wait = _avg(wait) or 0.0

    accounted = avg_compute + avg_network + avg_serialize + avg_sampling + avg_wait
    avg_idle = max(0.0, avg_total - accounted)

    return {
        "decode_ms_per_token": round(avg_total, 3),
        "hidden_transfer_ms_per_hop": round(avg_network / 2.0, 3),
        "serialize_ms_per_hop": round(avg_serialize / 2.0, 3),
        "pipeline_utilization_pct": round(100.0 * avg_compute / avg_total, 2),
        "worker_idle_pct": round(100.0 * (avg_wait + avg_idle) / avg_total, 2),
        "token_count": float(len(tokens)),
    }


def rollup_decode_buckets(
        span_bottleneck: dict[str, Any],
        ggml_summary: dict[str, Any] | None,
        tokens: list[dict[str, Any]],
) -> dict[str, Any]:
    cat_pct = dict(span_bottleneck.get("category_pct") or {})
    ggml_us = (ggml_summary or {}).get("event_us") or {}
    sched_us = float(ggml_us.get("SCHED_QUEUE_WAIT", 0) or 0)
    ggml_exec_us = float(ggml_us.get("GGML_GRAPH_EXECUTE", 0) or 0)
    ggml_build_us = float(ggml_us.get("GGML_GRAPH_BUILD", 0) or 0)

    token_m = token_decode_metrics(tokens)
    total_ms = float(token_m.get("decode_ms_per_token") or 0.0) * float(token_m.get("token_count") or 1.0)
    sched_ms = sched_us / 1000.0
    sched_pct = round(100.0 * sched_ms / max(total_ms, 0.001), 2) if total_ms > 0 else 0.0

    compute_pct = float(cat_pct.get("COMPUTE", 0.0))
    network_pct = float(cat_pct.get("NETWORK", 0.0))
    serialize_pct = float(cat_pct.get("SERIALIZATION", 0.0))
    wait_pct = float(cat_pct.get("WAIT", 0.0)) + float(cat_pct.get("IDLE", 0.0)) + sched_pct
    sampling_pct = float(cat_pct.get("SAMPLING", 0.0))
    unknown_pct = float(span_bottleneck.get("unknown_pct", 0.0))

    accounted = compute_pct + network_pct + serialize_pct + wait_pct + sampling_pct
    gap_pct = max(0.0, round(100.0 - accounted - unknown_pct, 2))
    idle_pct = round(wait_pct + gap_pct, 2)

    return {
        "buckets_pct": {
            "compute": round(compute_pct, 2),
            "network": round(network_pct, 2),
            "serialization": round(serialize_pct, 2),
            "sampling": round(sampling_pct, 2),
            "idle": idle_pct,
            "unknown": unknown_pct,
        },
        "ggml_us": {
            "SCHED_QUEUE_WAIT": int(sched_us),
            "GGML_GRAPH_EXECUTE": int(ggml_exec_us),
            "GGML_GRAPH_BUILD": int(ggml_build_us),
        },
        "token_metrics": token_m,
    }


def scheduler_wait_ms_per_token(ggml_doc: dict[str, Any], token_count: int) -> float | None:
    if token_count <= 0:
        return None
    sched_us = 0.0
    for ev in ggml_doc.get("events") or []:
        if ev.get("event") != "SCHED_QUEUE_WAIT" or ev.get("kind") != "span":
            continue
        if str(ev.get("phase", "")) not in ("decode", ""):
            continue
        dur = ev.get("dur_us")
        if isinstance(dur, (int, float)):
            sched_us += float(dur)
    if sched_us <= 0:
        return None
    return round(sched_us / 1000.0 / token_count, 3)


def collect_metrics(
        analysis_dir: Path,
        install_dir: Path | None = None,
        session_dir: Path | None = None,
        ttft_dir: Path | None = None,
) -> dict[str, Any]:
    trace = _read_json(analysis_dir / "trace.json")
    bottleneck = _read_json(analysis_dir / "bottleneck.json") or trace.get("bottleneck", {})
    ggml = _read_json(analysis_dir / "ggml.json")
    tokens = trace.get("tokens") if isinstance(trace.get("tokens"), list) else []

    metrics: dict[str, Any] = {}
    metrics.update(token_decode_metrics(tokens))
    token_n = int(metrics.get("token_count") or 0)

    ggml_summary = ggml.get("summary") if isinstance(ggml.get("summary"), dict) else {}
    sched_ms = scheduler_wait_ms_per_token(ggml, token_n)
    if sched_ms is not None:
        metrics["scheduler_wait_ms_per_token"] = sched_ms

    metrics["unknown_pct"] = float(bottleneck.get("unknown_pct", 0.0))

    if session_dir:
        session = _read_json(session_dir / "session.json")
        breakdown = session.get("breakdown") if isinstance(session.get("breakdown"), dict) else {}
        if breakdown.get("total_ms") is not None:
            metrics["session_create_ms"] = float(breakdown["total_ms"])

    if ttft_dir:
        ttft = _read_json(ttft_dir / "ttft.json")
        summary = ttft.get("summary") if isinstance(ttft.get("summary"), dict) else {}
        client = summary.get("client_ttft_ms")
        if isinstance(client, (int, float)):
            metrics["ttft_ms"] = float(client)
        elif summary.get("prefill_wall_ms") is not None:
            metrics["ttft_ms"] = float(summary["prefill_wall_ms"])

    if install_dir:
        reuse = _read_json(install_dir / "install_reuse.json")
        if reuse.get("reuse_pct") is not None:
            metrics["install_reuse_pct"] = float(reuse["reuse_pct"])

    mono = _read_json(analysis_dir / "mono_compare.json")
    if mono.get("overhead_pct") is not None:
        metrics["decode_overhead_pct"] = float(mono["overhead_pct"])

    metrics["rollup"] = rollup_decode_buckets(bottleneck, ggml_summary, tokens)
    return metrics


def evaluate_budgets(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, spec in BUDGET_TARGETS.items():
        value = metrics.get(key)
        target = spec.get("target")
        if value is None and spec.get("optional"):
            status: BudgetStatus = "SKIP"
        else:
            status = budget_status(
                float(value) if isinstance(value, (int, float)) else None,
                float(target) if isinstance(target, (int, float)) else None,
                str(spec.get("mode", "lte")),
            )
        delta_pct: float | None = None
        if isinstance(value, (int, float)) and isinstance(target, (int, float)) and target != 0:
            if spec.get("mode") == "gte":
                delta_pct = round(100.0 * (float(value) - target) / target, 1)
            else:
                delta_pct = round(100.0 * (float(value) - target) / target, 1)

        rows.append({
            "metric": key,
            "label": spec.get("label", key),
            "phase": spec.get("phase", ""),
            "value": value,
            "target": target,
            "unit": spec.get("unit", ""),
            "status": status,
            "delta_pct": delta_pct,
            "note": spec.get("note"),
        })
    return rows


def summarize_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for row in rows:
        status = str(row.get("status", "SKIP"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_report_md(document: dict[str, Any]) -> str:
    lines = [
        "# Task 12 Performance Report",
        "",
        f"**Trace:** `{document.get('analysis_dir', '')}`",
        "",
        "## Budget",
        "",
        "| Metric | Value | Target | Status |",
        "|--------|-------|--------|--------|",
    ]
    for row in document.get("budget", []):
        value = row.get("value")
        target = row.get("target")
        unit = row.get("unit", "")
        val_s = "—" if value is None else f"{value}{unit}"
        tgt_s = "—" if target is None else f"{target}{unit}"
        lines.append(
            f"| {row.get('label', '')} | {val_s} | {tgt_s} | **{row.get('status', 'SKIP')}** |"
        )

    rollup = (document.get("metrics") or {}).get("rollup", {})
    buckets = rollup.get("buckets_pct") if isinstance(rollup.get("buckets_pct"), dict) else {}
    if buckets:
        lines.extend([
            "",
            "## Decode Bottleneck",
            "",
        ])
        for name, pct in buckets.items():
            lines.append(f"- {name.capitalize():15} {pct}%")

    counts = document.get("status_counts") or {}
    lines.extend([
        "",
        "## Summary",
        "",
        f"- PASS: **{counts.get('PASS', 0)}**",
        f"- WARN: **{counts.get('WARN', 0)}**",
        f"- FAIL: **{counts.get('FAIL', 0)}**",
        f"- SKIP: **{counts.get('SKIP', 0)}**",
        "",
    ])
    return "\n".join(lines)


def merge_budget_analysis(
        analysis_dir: Path,
        out_dir: Path | None = None,
        *,
        install_dir: Path | None = None,
        session_dir: Path | None = None,
        ttft_dir: Path | None = None,
) -> dict[str, Any]:
    out = out_dir or analysis_dir
    out.mkdir(parents=True, exist_ok=True)

    metrics = collect_metrics(analysis_dir, install_dir, session_dir, ttft_dir)
    budget_rows = evaluate_budgets(metrics)
    status_counts = summarize_status(budget_rows)

    span_bottleneck = _read_json(analysis_dir / "bottleneck.json")
    if not span_bottleneck:
        trace = _read_json(analysis_dir / "trace.json")
        span_bottleneck = trace.get("bottleneck", {})

    enhanced_bottleneck = {
        **span_bottleneck,
        "rollup": metrics.get("rollup", {}),
        "budget": budget_rows,
        "status_counts": status_counts,
    }

    document = {
        "analysis_dir": str(analysis_dir),
        "metrics": metrics,
        "budget": budget_rows,
        "status_counts": status_counts,
        "bottleneck": enhanced_bottleneck,
    }

    (out / "budget.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    (out / "bottleneck.json").write_text(json.dumps(enhanced_bottleneck, indent=2), encoding="utf-8")
    (out / "report.md").write_text(build_report_md(document), encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 12 budget + bottleneck analysis")
    parser.add_argument("analysis_dir", type=Path, help="Decode analysis directory")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--install", type=Path, help="Install analysis directory")
    parser.add_argument("--session", type=Path, help="Session analysis directory")
    parser.add_argument("--ttft", type=Path, help="TTFT analysis directory")
    args = parser.parse_args()

    doc = merge_budget_analysis(
        args.analysis_dir,
        args.output,
        install_dir=args.install,
        session_dir=args.session,
        ttft_dir=args.ttft,
    )
    print(json.dumps({
        "status_counts": doc["status_counts"],
        "budget": [
            {k: row[k] for k in ("label", "value", "target", "status")}
            for row in doc["budget"]
            if row.get("status") != "SKIP"
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
