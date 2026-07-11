#!/usr/bin/env python3
"""Generate Markdown and HTML reports from benchmark results."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _stage_ms(scenario: dict[str, Any], name: str) -> float | None:
    for s in scenario.get("stages", []):
        if s.get("name") == name:
            return s.get("duration_ms")
    return None


def _stage_metric(scenario: dict[str, Any], stage: str, key: str) -> Any:
    for s in scenario.get("stages", []):
        if s.get("name") == stage:
            return s.get("metrics", {}).get(key)
    return None


def _bar(value: float, max_value: float, width: int = 20) -> str:
    if max_value <= 0:
        return ""
    filled = int(round(width * min(value, max_value) / max_value))
    return "█" * filled


def scenario_status(sc: dict[str, Any]) -> tuple[str, str]:
    if sc.get("skipped"):
        notes = sc.get("notes") or []
        return "SKIPPED", str(notes[0]) if notes else ""
    if sc.get("error"):
        return "FAIL", str(sc["error"])
    if sc.get("skipped_after_layout"):
        notes = sc.get("notes") or []
        return "SKIP_LAYOUT", str(notes[0]) if notes else "fits_cluster=false"
    gen = next((s for s in sc.get("stages", []) if s.get("name") == "generate"), None)
    if gen is not None:
        status = gen.get("metrics", {}).get("http_status")
        if status == 200:
            return "PASS", ""
        notes = gen.get("notes") or []
        return "FAIL", "; ".join(str(n) for n in notes) or f"generate HTTP {status}"
    sess = next((s for s in sc.get("stages", []) if s.get("name") == "session_create"), None)
    if sess is not None and sess.get("metrics", {}).get("http_status") != 200:
        notes = sess.get("notes") or []
        return "FAIL", "; ".join(str(n) for n in notes) or "session_create failed"
    sync = next((s for s in sc.get("stages", []) if s.get("name") == "synchronization"), None)
    if sync is not None:
        state = sync.get("metrics", {}).get("state", "")
        if state and state not in ("completed", "READY"):
            notes = sync.get("notes") or []
            return "FAIL", "; ".join(str(n) for n in notes) or f"sync state={state}"
    return "PARTIAL", ""


def summarize_scenario(sc: dict[str, Any]) -> dict[str, Any]:
    status, note = scenario_status(sc)
    return {
        "scenario_id": sc.get("scenario_id", ""),
        "model": sc.get("model_key", ""),
        "status": status,
        "status_note": note,
        "planner_ms": _stage_ms(sc, "layout"),
        "install_ms": _stage_ms(sc, "synchronization"),
        "coverage_ms": _stage_ms(sc, "coverage"),
        "materialization_ms": _stage_ms(sc, "materialization"),
        "session_ms": _stage_ms(sc, "session_create"),
        "generate_ms": _stage_ms(sc, "generate"),
        "generate_tps": _stage_metric(sc, "generate", "tokens_per_sec"),
        "prefill_ms": _stage_metric(sc, "generate", "prefill_ms"),
        "decode_ms": _stage_metric(sc, "generate", "decode_ms"),
        "fits_cluster": _stage_metric(sc, "layout", "fits_cluster"),
        "placements": _stage_metric(sc, "layout", "placement_count"),
    }


def build_markdown(document: dict[str, Any]) -> str:
    cluster = document.get("cluster", {})
    mem = cluster.get("memory", {})
    scenarios = document.get("scenarios", [])
    lines = [
        "# Cluster Benchmark",
        "",
        f"**Run ID:** `{document.get('run_id', '')}`  ",
        f"**Profile:** `{document.get('profile', '')}`  ",
        f"**Mode:** `{document.get('mode', '')}`  ",
        f"**Orchestrator:** `{document.get('orchestrator', '')}`  ",
        "",
        "## Cluster",
        "",
        f"- Nodes: **{cluster.get('node_count', 0)}**",
        f"- Free memory: **{mem.get('free_total_gb', '?')} GB** (RAM {mem.get('free_ram_gb', '?')} + VRAM {mem.get('free_vram_gb', '?')})",
        f"- Total memory: **{mem.get('total_total_gb', '?')} GB**",
        "",
    ]

    for n in cluster.get("nodes", []):
        lines.append(
            f"  - `{n.get('node_id')}` {n.get('gpu', '')} — "
            f"RAM {n.get('free_ram_gb')}/{n.get('total_ram_gb')} GB, "
            f"VRAM {n.get('free_vram_gb')}/{n.get('total_vram_gb')} GB"
        )

    lines.extend(["", "## Software", ""])
    sw = document.get("software", {})
    na = sw.get("node_agent", {})
    ll = sw.get("llama_cpp", {})
    lines.extend([
        f"- node-agent: `{na.get('sha', '')[:12]}` ({na.get('branch', '')})",
        f"- llama.cpp: `{ll.get('sha', '')[:12]}`",
        f"- build: **{sw.get('build_type', '')}** / backend **{sw.get('backend', '')}**",
        f"- OS: {sw.get('os', '')} {sw.get('arch', '')}",
        "",
        "## Scenarios",
        "",
        "| Model | Status | Planner | Install | Coverage | Materialize | Generate TPS | Fits |",
        "|-------|--------|---------|---------|----------|-------------|--------------|------|",
    ])

    for sc in scenarios:
        if sc.get("skipped"):
            lines.append(f"| {sc.get('model_key')} | SKIPPED | — | — | — | — | — | — |")
            continue
        sm = summarize_scenario(sc)
        lines.append(
            f"| {sm['model']} | {sm['status']} | {fmt_ms(sm['planner_ms'])} | {fmt_s(sm['install_ms'])} | "
            f"{fmt_ms(sm['coverage_ms'])} | {fmt_ms(sm['materialization_ms'])} | "
            f"{fmt_tps(sm['generate_tps'])} | {sm['fits_cluster']} |"
        )

    if scenarios:
        sc = next((s for s in scenarios if not s.get("skipped")), scenarios[0])
        sm = summarize_scenario(sc)
        lines.extend([
            "",
            "## Highlights (first scenario)",
            "",
            f"- Planner: **{fmt_ms(sm['planner_ms'])}** ({sm['placements']} placements)",
            f"- Install: **{fmt_s(sm['install_ms'])}**",
            f"- Coverage: **{fmt_ms(sm['coverage_ms'])}**",
            f"- Materialization: **{fmt_ms(sm['materialization_ms'])}**",
            f"- Generate: **{fmt_tps(sm['generate_tps'])}**",
            f"- Prefill: **{fmt_ms(sm['prefill_ms'])}** / Decode: **{fmt_ms(sm['decode_ms'])}** per token",
        ])

    summary = document.get("summary", {})
    if summary:
        lines.extend([
            "",
            "## Summary",
            "",
            f"- Scenarios: {summary.get('scenario_count', 0)}",
        ])
        status_counts = summary.get("status_counts") or {}
        if status_counts:
            counts = ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
            lines.append(f"- Status: {counts}")
        if summary.get("generate_tps_avg") is not None:
            lines.append(f"- Avg generate TPS: **{summary.get('generate_tps_avg')}**")
        if summary.get("generate_tps_max") is not None:
            lines.append(f"- Max generate TPS: **{summary.get('generate_tps_max')}**")

    perf = document.get("perf_trace")
    if isinstance(perf, dict) and perf.get("budget"):
        lines.extend(["", "## Runtime Perf Trace (Task 12)", ""])
        if perf.get("analysis_dir"):
            lines.append(f"- Analysis: `{perf['analysis_dir']}`")
        if perf.get("timeline_html"):
            lines.append(f"- Timeline: `{perf['timeline_html']}`")
        reg = perf.get("regression")
        if isinstance(reg, dict):
            lines.append(
                f"- Regression: PASS={reg.get('PASS', 0)} "
                f"WARN={reg.get('WARN', 0)} FAIL={reg.get('FAIL', 0)}"
            )
        lines.extend([
            "",
            "| Metric | Value | Target | Status |",
            "|--------|-------|--------|--------|",
        ])
        for row in perf.get("budget", []):
            if row.get("status") == "SKIP":
                continue
            val = row.get("value")
            tgt = row.get("target")
            val_s = "—" if val is None else f"{val}{row.get('unit', '')}"
            tgt_s = "—" if tgt is None else f"{tgt}{row.get('unit', '')}"
            lines.append(
                f"| {row.get('label', '')} | {val_s} | {tgt_s} | {row.get('status', '')} |"
            )
        buckets = perf.get("bottleneck_pct") or {}
        if buckets:
            parts = ", ".join(f"{k} {v}%" for k, v in sorted(buckets.items(), key=lambda x: -x[1]))
            lines.extend(["", f"**Decode buckets:** {parts}"])

    validation = perf.get("validation") if isinstance(perf, dict) else None
    if isinstance(validation, dict):
        lines.extend([
            "",
            "## Task 14 Runtime Observability",
            "",
            f"- Overall: **{validation.get('overall', 'UNKNOWN')}**",
            f"- Trace: `{validation.get('trace_id', '—')}`",
            "",
            "| Check | Status | Reason |",
            "|-------|--------|--------|",
        ])
        for row in validation.get("checks", []):
            reason = row.get("reason") or "—"
            lines.append(f"| {row.get('name', '')} | {row.get('status', '')} | {reason} |")
        metrics = validation.get("metrics") or {}

        def _metric_line(label: str, doc: dict[str, Any], value_keys: tuple[str, ...]) -> None:
            status = doc.get("status", "UNKNOWN")
            if status == "UNKNOWN":
                return
            val = None
            for key in value_keys:
                if doc.get(key) is not None:
                    val = doc.get(key)
                    break
            val_s = "—" if val is None else str(val)
            lines.append(f"- {label}: **{val_s}** ({status})")

        lines.append("")
        _metric_line("TPS (source of truth)", metrics.get("tps") or {}, ("value",))
        _metric_line("Ceiling TPS", metrics.get("ceiling_tps") or {}, ("value",))
        _metric_line("Bubble %", metrics.get("bubble") or {}, ("bubble_pct",))
        crit = metrics.get("critical_path") or {}
        if crit.get("status") != "UNKNOWN":
            cp = crit.get("avg_wall_critical_path_ms") or crit.get("avg_sum_compute_ms")
            lines.append(f"- Critical path (ms/token): **{cp if cp is not None else '—'}** ({crit.get('status', '—')})")
        cross = metrics.get("tps_vs_ceiling") or {}
        if cross.get("status") not in (None, "UNKNOWN", "SKIP"):
            lines.append(f"- TPS vs ceiling: **{cross.get('status', '—')}**")
        if validation.get("overall") == "INVALID":
            lines.extend([
                "",
                "> **METRIC INVALID** — report throughput/bubble figures are inconsistent. "
                "See `validation.md` before drawing performance conclusions.",
            ])

    lines.append("")
    return "\n".join(lines)


def fmt_ms(v: Any) -> str:
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v / 1000:.1f}s"
    return f"{v:.0f}ms"


def fmt_s(v: Any) -> str:
    if v is None:
        return "—"
    return f"{v / 1000:.1f}s"


def fmt_tps(v: Any) -> str:
    if v is None:
        return "—"
    return f"{v:.1f} tok/s"


def build_html(document: dict[str, Any]) -> str:
    cluster = document.get("cluster", {})
    mem = cluster.get("memory", {})
    scenarios = [s for s in document.get("scenarios", []) if not s.get("skipped")]
    summaries = [summarize_scenario(s) for s in scenarios]

    def chart_row(label: str, values: list[float | None], unit: str = "ms") -> str:
        nums = [v for v in values if isinstance(v, (int, float))]
        mx = max(nums) if nums else 1
        rows = []
        for sc, val in zip(scenarios, values):
            if not isinstance(val, (int, float)):
                continue
            bar = _bar(val, mx, 24)
            display = fmt_s(val) if unit == "s" else (fmt_tps(val) if unit == "tps" else fmt_ms(val))
            rows.append(
                f"<tr><td>{html.escape(sc.get('model_key', ''))}</td>"
                f"<td><code>{bar}</code></td><td>{display}</td></tr>"
            )
        return f"<h3>{html.escape(label)}</h3><table><tbody>{''.join(rows)}</tbody></table>"

    planner_vals = [s["planner_ms"] for s in summaries]
    install_vals = [s["install_ms"] for s in summaries]
    tps_vals = [s["generate_tps"] for s in summaries]

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Cluster Benchmark {html.escape(document.get('run_id', ''))}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #0f1115; color: #e8eaed; }}
    h1, h2, h3 {{ color: #8ab4f8; }}
    table {{ border-collapse: collapse; margin: 1rem 0; }}
    td, th {{ padding: 0.4rem 0.8rem; border-bottom: 1px solid #333; }}
    code {{ color: #81c995; }}
    .meta {{ color: #9aa0a6; }}
  </style>
</head>
<body>
  <h1>Cluster Benchmark</h1>
  <p class="meta">Run <code>{html.escape(document.get('run_id', ''))}</code> |
     profile <code>{html.escape(document.get('profile', ''))}</code> |
     {cluster.get('node_count', 0)} nodes |
     {mem.get('free_total_gb', '?')} GB free</p>
  {chart_row('Planner time', planner_vals)}
  {chart_row('Install', install_vals, unit='s')}
  {chart_row('Generate TPS', tps_vals, unit='tps')}
</body>
</html>
"""
    return body


def write_reports(out_dir: Path, document: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(build_markdown(document), encoding="utf-8")
    (out_dir / "report.html").write_text(build_html(document), encoding="utf-8")


def load_results(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
